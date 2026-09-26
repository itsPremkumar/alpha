"""Built-in deep_web_search tool ported from AgentEye's research engine.

Ports ``agent_eye/research_engine.py`` (Agent Search Lite, MIT): query
expansion -> keyless search per query -> URL dedup -> relevance ranking ->
findings, verified citations, confidence and summary, layered on top of the
ported keyless chain in ``keyless_web_search_tool``. Zero new hard
dependencies (httpx only, plus the optional ddgs fallback it inherits).

Honesty contract (never fabricated research):
- ``status: "ok"``         -- at least one underlying keyless search returned results.
- ``status: "no_results"`` -- the searches ran and found nothing: empty
  findings/citations, ``confidence`` 0.0, explicit summary saying so.
- ``status: "failed"``     -- every underlying search failed: the payload
  carries ``error``/``failures`` only -- no findings, citations, summary or
  confidence key is emitted rather than inventing them.
- Individual expanded-query failures are always disclosed in ``partial_failures``,
  including expanded queries skipped because the run's wall-clock budget ran out.
- The tool never fetches the pages it cites, so every citation is marked
  ``content_retrieved: false`` and the domain check is labelled as such.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from langchain.tools import tool

from .keyless_web_search_tool import DEFAULT_TIMEOUT, _search_keyless

#: Ported from research_engine.verify_source: known domain -> (category, reliable).
_RELIABLE_DOMAINS: dict[str, tuple[str, bool]] = {
    "github.com": ("code", True),
    "stackoverflow.com": ("code", True),
    "arxiv.org": ("academic", True),
    "pubmed.ncbi.nlm.nih.gov": ("academic", True),
    "wikipedia.org": ("encyclopedia", True),
    "news.ycombinator.com": ("tech_news", True),
    "bbc.com": ("news", True),
    "reuters.com": ("news", True),
    "nature.com": ("academic", True),
    "science.org": ("academic", True),
}

#: Wall-clock budget for the whole multi-query run. Each expanded query walks the
#: keyless fallback chain synchronously, so an unbounded fan-out can block for
#: minutes. When the budget runs out the remaining queries are reported as
#: skipped rather than silently dropped.
DEFAULT_BUDGET_SECONDS = 120.0
MAX_BUDGET_SECONDS = 600.0

#: What the per-citation verification block actually checks. These citations are
#: built from search results; the tool never fetches the pages it cites.
_VERIFICATION_SCOPE = "domain allowlist only; the page body was not retrieved, so this says nothing about the page's content"


def _expand_queries(question: str, depth: int) -> list[str]:
    """Port of research_engine._expand_query with order-preserving dedup."""
    lowered = question.lower()
    queries = [question]
    if "compare" in lowered:
        queries.extend((question.replace("compare", "vs"), question.replace("compare", "difference between")))
    if "best" in lowered:
        queries.append(question.replace("best", "top"))
    if "how" in lowered:
        queries.extend((question.replace("how", "guide"), question.replace("how", "tutorial")))
    if depth >= 2:
        current_year = time.localtime().tm_year
        queries.extend((f"{question} {current_year} {current_year + 1}", f"{question} latest"))
    if depth >= 3:
        queries.extend((f"{question} research paper", f"{question} analysis"))
    return list(dict.fromkeys(queries))


def _dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop later results that repeat an already-seen URL (port of _deduplicate_results)."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for result in results:
        url = result.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(result)
    return unique


def _rank(results: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    """Lexical relevance ranking (port of _rank_by_relevance; score disclosed per item)."""
    question_words = set(question.lower().split())
    for result in results:
        title = result.get("title", "").lower()
        description = result.get("description", "").lower()
        title_matches = sum(1 for word in question_words if word in title)
        description_matches = sum(1 for word in question_words if word in description)
        result["relevance_score"] = (title_matches * 2 + description_matches) / max(len(question_words), 1)
    results.sort(key=lambda item: item.get("relevance_score", 0), reverse=True)
    return results


def _normalized_host(url: str) -> str:
    """Lowercase host of a URL with any leading ``www.`` label removed."""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _host_is(host: str, known: str) -> bool:
    """True only for the domain itself or a real subdomain of it.

    A bare ``endswith`` is spoofable: ``notarxiv.org`` and
    ``arxiv.org.evil.test`` both end with ``arxiv.org``, so a suffix match
    would hand an attacker a citation marked ``reliable: true, category:
    academic``. Matching on label boundaries closes that.
    """
    return host == known or host.endswith("." + known)


def _verify_source(url: str) -> dict[str, Any]:
    """Port of research_engine.verify_source: local domain authority check, no network."""
    verification: dict[str, Any] = {
        "url": url,
        "domain": "",
        "reliable": False,
        "category": "",
        "scope": _VERIFICATION_SCOPE,
        "notes": [],
    }
    domain = _normalized_host(url)
    verification["domain"] = domain
    for known, (category, reliable) in _RELIABLE_DOMAINS.items():
        if _host_is(domain, known):
            verification["category"] = category
            verification["reliable"] = reliable
            break
    if not verification["category"]:
        verification["category"] = "unknown"
        verification["notes"].append("Unknown domain - verify manually")
    return verification


def _extract_findings(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Port of _extract_findings: trimmed per-source findings."""
    findings: list[dict[str, Any]] = []
    for result in results:
        findings.append(
            {
                "source": result.get("source", ""),
                "url": result.get("url", ""),
                "title": result.get("title", ""),
                "snippet": result.get("description", "")[:200],
                "relevance": result.get("relevance_score", 0),
            }
        )
    return findings


def _generate_citations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Port of _generate_citations plus the verify_source enrichment.

    Each citation states that the page was not retrieved, so a citation here can
    never be read as "this page was read and supports the finding".
    """
    citations: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        citations.append(
            {
                "id": index + 1,
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "source": result.get("source", ""),
                "accessed": time.strftime("%Y-%m-%d"),
                "content_retrieved": False,
                "verification": _verify_source(result.get("url", "")),
            }
        )
    return citations


def _calculate_confidence(findings: list[dict[str, Any]], target_sources: int) -> float:
    """Port of _calculate_confidence, clamped to <= 1.0 (a confidence > 1 would be dishonest)."""
    if not findings:
        return 0.0
    source_ratio = min(len(findings) / target_sources, 1.0)
    average_relevance = sum(item.get("relevance", 0) for item in findings) / len(findings)
    return round(min(source_ratio * 0.5 + average_relevance * 0.5, 1.0), 2)


def _generate_summary(findings: list[dict[str, Any]], question: str) -> str:
    """Port of _generate_summary: deterministic top-5 synthesis of the findings."""
    if not findings:
        return "No findings available."
    parts = [f"Research on: {question}\n", f"Based on {len(findings)} sources:\n"]
    for index, finding in enumerate(findings[:5]):
        parts.append(f"{index + 1}. {finding['title']}")
        if finding.get("snippet"):
            parts.append(f"   {finding['snippet'][:100]}...")
        parts.append("")
    return "\n".join(parts)


def _deep_web_search(question: str, sources: int, depth: int, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict[str, Any]:
    """Run expanded keyless research and return an honestly labelled payload (see module docstring)."""
    question = (question or "").strip()
    if not question:
        return {"status": "failed", "question": question, "error": "empty question: there is nothing to research"}
    requested_sources = _safe_int(sources, 10)
    requested_depth = _safe_int(depth, 2)
    try:
        requested_budget = float(budget_seconds)
    except (TypeError, ValueError):
        requested_budget = DEFAULT_BUDGET_SECONDS
    sources = max(3, min(requested_sources, 25))
    depth = max(1, min(requested_depth, 3))
    budget = max(1.0, min(requested_budget, MAX_BUDGET_SECONDS))
    clamped = [
        note
        for note in (
            f"sources clamped from {requested_sources} to {sources} (allowed range 3-25)" if sources != requested_sources else "",
            f"depth clamped from {requested_depth} to {depth} (allowed range 1-3)" if depth != requested_depth else "",
            f"budget clamped from {requested_budget} to {budget:g}s (allowed range 1-{MAX_BUDGET_SECONDS:g})" if budget != requested_budget else "",
        )
        if note
    ]
    queries = _expand_queries(question, depth)[: depth * 3]
    per_query_limit = max(1, sources // len(queries) + 1)
    collected: list[dict[str, Any]] = []
    query_attempts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    ran = 0
    started = time.monotonic()
    for expanded in queries:
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            failures.append({"query": expanded, "error": "skipped: the run's wall-clock budget was exhausted before this expanded query started"})
            continue
        outcome = _search_keyless(expanded, limit=per_query_limit, timeout=max(1.0, min(DEFAULT_TIMEOUT, remaining)))
        query_attempts.append({"query": expanded, "status": outcome["status"], "engine": outcome.get("engine"), "count": len(outcome.get("results", []))})
        if outcome["status"] == "failed":
            failure: dict[str, Any] = {"query": expanded, "error": outcome.get("error", "unknown error")}
            if "missing_package" in outcome:
                failure["missing_package"] = outcome["missing_package"]
            failures.append(failure)
            continue
        ran += 1
        collected.extend(outcome.get("results", []))
    bounds = {
        "sources_requested": requested_sources,
        "sources_effective": sources,
        "depth_requested": requested_depth,
        "depth_effective": depth,
        "expanded_queries_planned": len(queries),
        "expanded_queries_executed": len(query_attempts),
        "budget_seconds_requested": requested_budget,
        "budget_seconds_effective": budget,
        "budget_exhausted": len(query_attempts) < len(queries),
        "clamped": clamped,
    }
    if ran == 0:
        payload: dict[str, Any] = {
            "status": "failed",
            "question": question,
            "error": "every expanded search failed; no findings, citations, summary or confidence are fabricated",
            "query_attempts": query_attempts,
            "failures": failures,
            "bounds": bounds,
        }
        if failures and "missing_package" in failures[0]:
            payload["missing_package"] = failures[0]["missing_package"]
        return payload
    ranked = _rank(_dedupe(collected), question)[:sources]
    shared: dict[str, Any] = {
        "question": question,
        "query_attempts": query_attempts,
        "partial_failures": failures,
        "bounds": bounds,
    }
    if not ranked:
        return {
            "status": "no_results",
            **shared,
            "findings": [],
            "citations": [],
            "confidence": 0.0,
            "sources_consulted": 0,
            "summary": "No results found for the expanded queries; the searches ran and found nothing.",
        }
    findings = _extract_findings(ranked)
    return {
        "status": "ok",
        **shared,
        "findings": findings,
        "citations": _generate_citations(ranked),
        "confidence": _calculate_confidence(findings, sources),
        "sources_consulted": len(ranked),
        "summary": _generate_summary(findings, question),
    }


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@tool("deep_web_search", parse_docstring=True)
def deep_web_search(question: str, sources: int = 10, depth: int = 2, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> str:
    """Research a question across the keyless web-search chain and return findings with citations.

    Ports AgentEye's research engine: expands the question into related
    queries (depth 1-3), runs the keyless fallback chain for each, deduplicates
    and ranks results, then returns JSON with ``findings``, ``citations``
    (each carrying a local domain-verification block), ``confidence`` and a
    ``summary``. The ``status`` is honestly "ok", "no_results" (searches ran,
    zero matches, confidence 0.0) or "failed" (every underlying search failed:
    only ``error``/``failures`` are returned -- no findings, citations, summary
    or confidence are invented). Failures of individual expanded queries are
    disclosed in ``partial_failures``.

    Provenance: this tool never fetches the pages it cites. Every citation
    carries ``"content_retrieved": false`` and a ``verification.scope`` saying
    the check is a domain allowlist only, and each finding is the search
    provider's snippet.

    Args:
        question: The research question to investigate.
        sources: Maximum distinct sources to consult (3-25).
        depth: Query-expansion depth (1-3); higher depth searches more variants.
        budget_seconds: Wall-clock budget for the whole run (1-600). Expanded
            queries not started before it runs out are listed in
            ``partial_failures`` with reason ``budget exhausted``.
    """
    return json.dumps(
        _deep_web_search(question, sources=sources, depth=depth, budget_seconds=budget_seconds),
        indent=2,
    )
