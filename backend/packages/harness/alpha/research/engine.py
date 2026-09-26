"""Advanced Deep Research Engine for Alpha.

Implements multi-lane search planning (5-Pass Strategy), autonomous content fetching,
bounded gap analysis, adversarial source juxtaposition, and evidence-status-aware
Markdown report synthesis.

Provenance contract (every claim is traceable to content that was really read):

- ``EvidenceSource.retrieval`` records *what text the engine actually holds* for
  that source: ``page`` (the page body was fetched and screened) or
  ``snippet_only`` (the page was discovered but never read — the fetch failed, or
  the body was quarantined by the prompt-injection screen, so only the
  search-provider snippet remains).
- A claim is only given a page citation ``[[S3]](url)`` when the engine holds the
  page. A claim backed only by a snippet is rendered as a snippet and says so, in
  place of a page citation. A URL the engine never read can therefore never be
  presented as a page citation.
- Every lane, fetch, quarantine and deadline failure is recorded as a
  :class:`ResearchFailure` and rendered into the report, so a partial
  investigation is labelled partial instead of being presented as finished.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from alpha.research.five_pass import (
    FivePassSearchCompiler,
    SearchPassType,
)

logger = logging.getLogger(__name__)

#: Fetched content at or above this injection risk is dropped in favour of the
#: provider snippet. Below it the source is kept and only flagged — a page that
#: merely mentions "AI assistant" should not silently lose its evidence.
INJECTION_QUARANTINE_AT = 0.6

#: How many extracted findings per source get a support check. One request
#: covers them all, but a source with 40 findings is not 40x more useful.
MAX_VERIFIED_FINDINGS = 8

#: Hard cap on the characters of one page the engine will hold. Retrieved web
#: text is third-party data: unbounded it is a context blowup and an injection
#: surface. Any truncation is recorded on the source and disclosed in the report.
MAX_SOURCE_CHARS = 50_000

#: Results requested per first-wave search lane (5 lanes -> at most 20 candidates).
LANE_RESULTS = 4

#: Results requested per gap-resolution lane, and the most gap lanes ever run.
GAP_LANE_RESULTS = 3
MAX_GAP_LANES = 3

#: Explicit, disclosed ceilings on the public research surface. Exceeding one
#: never fails the run: the value is clamped and the clamp is reported in
#: ``report.bounds`` and in the report's coverage section.
MAX_RESEARCH_DEPTH = 5
MAX_RESEARCH_SOURCES = 30

#: Wall-clock ceiling on one research run, in seconds. A run that exceeds it
#: synthesises what it already retrieved and says so, rather than hanging.
DEFAULT_RESEARCH_DEADLINE_SECONDS = 300.0
MAX_RESEARCH_DEADLINE_SECONDS = 1_800.0

#: Most source pairs the report will name as candidate conflicts.
MAX_REPORTED_CONFLICTS = 8

#: ``EvidenceSource.retrieval`` values.
RETRIEVAL_PAGE = "page"
RETRIEVAL_SNIPPET_ONLY = "snippet_only"

#: ``ContradictionFinding.kind`` values. A conflict is a lexical disagreement
#: signal between two retrieved sources and is always quoted on both sides; a
#: juxtaposition is merely two sources shown side by side.
CONFLICT = "stated_conflict"
JUXTAPOSITION = "juxtaposition"

#: Short words carry no topical signal for conflict detection.
_STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    few for from further had has have having he her here hers him his how i if in into is
    it its itself just me more most my no nor not now of off on once only or other ought
    our ours out over own same she should so some such than that the their theirs them
    then there these they this those through to too under until up very was we were what
    when where which while who whom why will with would you your yours
    """.split()
)

#: Polarity markers. A subject one side talks about and the other denies is the
#: cheapest honest way to surface a disagreement between two retrieved sources
#: without a model in the loop.
_NEGATION_MARKERS = frozenset(
    """
    not no never cannot without unable fails failed failing failure worse slower
    regression regressed contradicted disputed debunked false myth unproven denied
    denies impossible lacks degraded incorrect inaccurate invalid broken crash leak
    overflow exhausted unavailable unsupported misleading overstated
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\d[\d.,%]*")

#: Measurable attributes. A disagreement is only worth surfacing when the two
#: sources are talking about the same measurable thing -- otherwise every pair
#: that happens to share a noun ("the decoder", "the product") would be
#: reported as a conflict, which trains the reader to ignore the section.
_CLAIM_ATTRIBUTES = frozenset(
    """
    latency throughput yield speedup speed cost price accuracy precision recall reliability
    availability uptime downtime performance memory bandwidth capacity scale scaling
    error errors failures failure rate ratio duration power energy temperature weight size
    retention conversion revenue growth coverage support usage
    """.split()
)


@dataclass
class ResearchFailure:
    """One disclosed failure: a lane, a retrieval, a quarantine or the budget.

    Every entry names the stage and the target so a reader can tell *what* was
    not researched, not merely that "something went wrong".
    """

    stage: str
    target: str = ""
    error_type: str = ""
    error: str = ""
    pass_type: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "pass_type": self.pass_type,
            "target": self.target,
            "error_type": self.error_type,
            "error": self.error,
        }

    def to_line(self) -> str:
        scope = f"`{self.pass_type}`" if self.pass_type else f"`{self.stage}`"
        target = f" — {self.target}" if self.target else ""
        kind = f"{self.error_type}: " if self.error_type else ""
        return f"- {scope}{target} — {self.stage} failed ({kind}{self.error})"


@dataclass
class EvidenceSource:
    """A verified source of evidence gathered during research."""

    url: str
    title: str
    snippet: str = ""
    content: str = ""
    pass_type: str = SearchPassType.DISCOVERY.value
    key_findings: list[str] = field(default_factory=list)
    extracted_facts: list[str] = field(default_factory=list)
    extracted_metrics: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, str] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)
    source_id: str = "S1"
    confidence: float = 0.9
    injection_risk: float | None = None
    injection_signals: list[str] = field(default_factory=list)
    unsupported_findings: list[str] = field(default_factory=list)
    citation_status: str = "not_checked"
    #: What text this source really carries. See the module docstring.
    retrieval: str = RETRIEVAL_SNIPPET_ONLY
    retrieval_note: str = "page not retrieved yet"
    content_truncated: bool = False
    content_chars_original: int = 0

    def __post_init__(self):
        if hasattr(self.pass_type, "value"):
            self.pass_type = self.pass_type.value
        if self.extracted_facts and not self.key_findings:
            self.key_findings = list(self.extracted_facts)
        elif self.key_findings and not self.extracted_facts:
            self.extracted_facts = list(self.key_findings)
        if self.metrics and not self.extracted_metrics:
            self.extracted_metrics = dict(self.metrics)
        elif self.extracted_metrics and not self.metrics:
            self.metrics = dict(self.extracted_metrics)

    @property
    def domain(self) -> str:
        try:
            parsed = urlparse(self.url)
            return parsed.netloc or self.url
        except Exception:
            return self.url

    @property
    def page_retrieved(self) -> bool:
        """True only when the page body itself was fetched and kept."""
        return self.retrieval == RETRIEVAL_PAGE

    def to_citation(self) -> str:
        return f"[{self.source_id}] {self.title} - {self.url}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "domain": self.domain,
            "pass_type": self.pass_type,
            "key_findings": self.key_findings,
            "extracted_metrics": self.extracted_metrics,
            "confidence": self.confidence,
            "citation_status": self.citation_status,
            "retrieval": self.retrieval,
            "retrieval_note": self.retrieval_note,
            "injection_risk": self.injection_risk,
            "injection_signals": self.injection_signals,
            "unsupported_findings": self.unsupported_findings,
            "content_truncated": self.content_truncated,
            "content_chars": len(self.content or ""),
            "content_chars_original": self.content_chars_original or len(self.content or ""),
            "fetched_at": self.fetched_at,
        }


@dataclass
class ResearchGap:
    """An identified knowledge gap or uncertainty requiring deeper search."""

    subtopic: str
    rationale: str
    suggested_query: str
    priority: int = 1
    resolved: bool = False
    resolution_notes: str = ""


@dataclass
class ContradictionFinding:
    """A disagreement between two retrieved sources, quoted on both sides."""

    claim: str
    source_a_title: str
    source_a_url: str
    source_b_title: str
    source_b_url: str
    nuance_explanation: str
    adversarial_evidence: str = ""
    #: ``stated_conflict`` when the two sources disagree about a shared subject;
    #: ``juxtaposition`` when they are only shown side by side.
    kind: str = JUXTAPOSITION
    subject: str = ""
    evidence_a: str = ""
    evidence_b: str = ""

    @property
    def is_conflict(self) -> bool:
        return self.kind == CONFLICT

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "kind": self.kind,
            "subject": self.subject,
            "source_a": f"{self.source_a_title} ({self.source_a_url})",
            "source_b": f"{self.source_b_title} ({self.source_b_url})",
            "evidence_a": self.evidence_a,
            "evidence_b": self.evidence_b,
            "nuance": self.nuance_explanation,
        }


@dataclass
class DeepResearchReport:
    """The completed comprehensive deep research report."""

    topic: str
    executive_summary: str
    core_findings: list[str]
    comparative_analysis: str
    adversarial_findings: str
    contradictions: list[ContradictionFinding]
    sources: list[EvidenceSource]
    citations: list[dict[str, str]]
    markdown_content: str
    status: str = "completed"
    depth: int = 3
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    #: Every disclosed failure, in the order it happened.
    failures: list[ResearchFailure] = field(default_factory=list)
    #: ``complete``, ``partial`` or ``none``. Never a bare "completed".
    coverage: str = "unknown"
    #: The limits actually applied to this run, including any clamp.
    bounds: dict[str, Any] = field(default_factory=dict)

    @property
    def key_findings(self) -> list[str]:
        return self.core_findings

    @property
    def markdown_report(self) -> str:
        return self.markdown_content

    @property
    def overall_confidence(self) -> float:
        """Mean confidence of the sources actually gathered (0.0 when none).

        Historically this returned a hardcoded ``0.92`` regardless of the
        evidence; it is now derived from the real per-source confidences.
        """
        if not self.sources:
            return 0.0
        return round(sum(s.confidence for s in self.sources) / len(self.sources), 4)

    @property
    def verified_citation_count(self) -> int:
        """Count sources with at least one semantically supported finding."""

        return sum(1 for source in self.sources if source.citation_status == "verified")

    @property
    def page_backed_source_count(self) -> int:
        """Sources whose page body the engine actually read."""
        return sum(1 for source in self.sources if source.page_retrieved)

    @property
    def snippet_only_source_count(self) -> int:
        """Sources the engine knows only from a search snippet."""
        return sum(1 for source in self.sources if not source.page_retrieved)

    @property
    def conflicts(self) -> list[ContradictionFinding]:
        return [c for c in self.contradictions if c.is_conflict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "coverage": self.coverage,
            "topic": self.topic,
            "executive_summary": self.executive_summary,
            "core_findings": self.core_findings,
            "comparative_analysis": self.comparative_analysis,
            "adversarial_findings": self.adversarial_findings,
            "contradictions": [c.to_dict() for c in self.contradictions],
            "adversarial_comparisons": [c.to_dict() for c in self.contradictions],
            "sources_count": len(self.sources),
            "sources_read": self.page_backed_source_count,
            "sources_snippet_only": self.snippet_only_source_count,
            "citations_count": len(self.citations),
            "citations_verified": self.verified_citation_count,
            "conflicts_detected": len(self.conflicts),
            "failures": [f.to_dict() for f in self.failures],
            "bounds": self.bounds,
            "generated_at": self.generated_at,
            "markdown_content": self.markdown_content,
        }


SearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
FetchFn = Callable[[str], Awaitable[str]]


@dataclass
class _Collection:
    """Mutable state of one research run, so a deadline can still synthesise."""

    discovered: dict[str, EvidenceSource] = field(default_factory=dict)
    lanes_planned: int = 0
    lanes_searched: int = 0


def _clamp_depth(depth: int) -> tuple[int, str | None]:
    try:
        requested = int(depth)
    except (TypeError, ValueError):
        return 3, f"depth {depth!r} is not an integer; using 3"
    clamped = max(1, min(requested, MAX_RESEARCH_DEPTH))
    if clamped != requested:
        return clamped, f"depth {requested} clamped to {clamped} (allowed range 1-{MAX_RESEARCH_DEPTH})"
    return clamped, None


def _clamp_max_sources(max_sources: int) -> tuple[int, str | None]:
    try:
        requested = int(max_sources)
    except (TypeError, ValueError):
        return 15, f"max_sources {max_sources!r} is not an integer; using 15"
    clamped = max(1, min(requested, MAX_RESEARCH_SOURCES))
    if clamped != requested:
        return clamped, f"max_sources {requested} clamped to {clamped} (allowed range 1-{MAX_RESEARCH_SOURCES})"
    return clamped, None


def _clamp_deadline(seconds: float) -> tuple[float, str | None]:
    try:
        requested = float(seconds)
    except (TypeError, ValueError):
        return DEFAULT_RESEARCH_DEADLINE_SECONDS, f"deadline_seconds {seconds!r} is not a number; using {DEFAULT_RESEARCH_DEADLINE_SECONDS:.0f}s"
    clamped = max(1.0, min(requested, MAX_RESEARCH_DEADLINE_SECONDS))
    if clamped != requested:
        return clamped, f"deadline_seconds {requested} clamped to {clamped:g} (allowed range 1-{MAX_RESEARCH_DEADLINE_SECONDS:g})"
    return clamped, None


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD_RE.findall((text or "").lower())}


def _subjects(text: str) -> set[str]:
    return {token for token in _tokens(text) if len(token) >= 4 and token not in _STOPWORDS and token not in _NEGATION_MARKERS}


def _negations(text: str) -> set[str]:
    return _tokens(text) & _NEGATION_MARKERS


def _measures(text: str) -> set[str]:
    """The measurable things a statement talks about (attributes and numbers)."""
    tokens = _tokens(text)
    return (tokens & _CLAIM_ATTRIBUTES) | {number.rstrip(".,%") for number in _NUMBER_RE.findall(text or "")}


def _claims_of(source: EvidenceSource) -> list[str]:
    """The statements a source is treated as making, best text first."""
    claims = [f for f in source.key_findings if f and f.strip()]
    if not claims and source.snippet:
        claims = [source.snippet]
    if not claims and source.content:
        claims = [line.strip() for line in source.content.split("\n") if len(line.strip()) > 30][:2]
    return claims


def _pick_conflict(a_claim: str, b_claim: str) -> tuple[str, str] | None:
    """Return ``(subject, marker)`` when two claims disagree about a measure.

    The test is deliberately lexical and conservative. Both statements must talk
    about a shared non-trivial subject *and* about the same measurable thing
    (a shared attribute word or a shared number), and the polarity markers must
    fire on exactly one side. It proposes a subject for review; it never decides
    who is right, and the report says so wherever a conflict is shown.
    """
    shared = _subjects(a_claim) & _subjects(b_claim)
    if not shared:
        return None
    measures = _measures(a_claim) & _measures(b_claim)
    if not measures:
        return None
    a_neg = _negations(a_claim)
    b_neg = _negations(b_claim)
    if not a_neg and not b_neg:
        return None
    if bool(a_neg) == bool(b_neg):
        return None
    subject = sorted(shared & measures, key=lambda token: (-len(token), token))[0]
    if not subject:
        subject = sorted(shared, key=lambda token: (-len(token), token))[0]
    marker = sorted(a_neg or b_neg)[0]
    return subject, marker


def _coverage(
    collection: _Collection,
    failures: list[ResearchFailure],
    sources: list[EvidenceSource],
    *,
    adversarial_planned: bool = False,
) -> str:
    """Label a run ``complete`` only when the promised facets really arrived.

    A run is ``partial`` when a lane failed, a page could not be read, a page
    was quarantined, content was cut short, a candidate was dropped by the
    source cap, or the adversarial/falsification facet was planned but returned
    nothing. Only a run that planned and received every facet it promised is
    ``complete``.
    """
    if not sources:
        return "none"
    lossy_stages = {"search", "fetch", "deadline", "injection_quarantine", "truncation"}
    if collection.lanes_searched < collection.lanes_planned:
        return "partial"
    if any(f.stage in lossy_stages for f in failures):
        return "partial"
    if any(s.content_truncated for s in sources):
        return "partial"
    if not all(s.page_retrieved for s in sources):
        return "partial"
    if adversarial_planned and not any(s.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION.value or "adversarial" in s.pass_type for s in sources):
        return "partial"
    return "complete"


class DeepResearchEngine:
    """Autonomous multi-hop Deep Research engine.

    Executes a 5-pass search strategy, fetches full source content,
    performs targeted gap analysis, juxtaposes adversarial evidence, and
    synthesizes cited Markdown reports with explicit support status.
    """

    def __init__(
        self,
        search_fn: SearchFn | None = None,
        fetch_fn: FetchFn | None = None,
        domain_context: str = "technology & software engineering",
    ):
        # No silent mock fallback: an unconfigured engine researches the live
        # web through :mod:`alpha.research.backends` and raises honestly if no
        # backend can be loaded. The mock providers only run when the caller
        # passes them explicitly (tests and offline fixtures).
        if search_fn is None or fetch_fn is None:
            from alpha.research.backends import default_fetch_fn, default_search_fn

            search_fn = search_fn or default_search_fn()
            fetch_fn = fetch_fn or default_fetch_fn()
        self.search_fn = search_fn
        self.fetch_fn = fetch_fn
        self.domain_context = domain_context

    @staticmethod
    async def mock_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Deterministic offline search provider (tests/fixtures only).

        Must be passed explicitly as ``search_fn``; never used as a default,
        because silently fabricated ``example.org`` sources were previously
        reported as verified research.
        """
        q_slug = re.sub(r"[^a-zA-Z0-9]+", "-", query.lower()).strip("-")
        return [
            {
                "title": f"Comprehensive Guide to {query[:30]}",
                "url": f"https://example.org/research/{q_slug}-guide",
                "snippet": f"Overview, architectural patterns, and verified data for {query}.",
            },
            {
                "title": f"Empirical Benchmarks: {query[:30]}",
                "url": f"https://example.org/benchmarks/{q_slug}",
                "snippet": f"Performance comparisons, metrics, and production evaluations of {query}.",
            },
        ][:max_results]

    @staticmethod
    async def mock_fetch(url: str) -> str:
        """Deterministic offline fetcher (tests/fixtures only).

        Must be passed explicitly as ``fetch_fn``; never used as a default.
        """
        domain = urlparse(url).netloc or "example.org"
        return (
            f"# Source Documentation from {domain}\n\n"
            f"This source provides in-depth technical analysis for URL: {url}.\n\n"
            f"Key metrics include 99.9% reliability, 4x latency reduction, and multi-tenant security.\n"
            f"Edge-case considerations highlight network partitions and memory bounds under high concurrency."
        )

    async def run_research(
        self,
        topic: str,
        depth: int = 3,
        max_sources: int = 15,
        include_adversarial: bool = True,
        deadline_seconds: float = DEFAULT_RESEARCH_DEADLINE_SECONDS,
    ) -> DeepResearchReport:
        """Execute autonomous deep research on a topic.

        ``depth``, ``max_sources`` and ``deadline_seconds`` are clamped to the
        documented ceilings; any clamp is reported in ``report.bounds`` and in
        the report's coverage section rather than being applied silently.
        """
        topic_clean = topic.strip()
        effective_depth, depth_note = _clamp_depth(depth)
        effective_max_sources, sources_note = _clamp_max_sources(max_sources)
        deadline, deadline_note = _clamp_deadline(deadline_seconds)

        search_plan = FivePassSearchCompiler.compile(
            question=topic_clean,
            domain_context=self.domain_context,
        )
        lanes = list(search_plan.lanes)
        if not include_adversarial:
            lanes = [lane for lane in lanes if lane.pass_type != SearchPassType.ADVERSARIAL_CONTRADICTION]

        logger.info(
            "Starting deep research for: %s (depth=%d, max_sources=%d, deadline=%.0fs)",
            topic_clean,
            effective_depth,
            effective_max_sources,
            deadline,
        )

        failures: list[ResearchFailure] = []
        collection = _Collection(lanes_planned=len(lanes))
        try:
            await asyncio.wait_for(
                self._collect(
                    topic=topic_clean,
                    lanes=lanes,
                    depth=effective_depth,
                    max_sources=effective_max_sources,
                    collection=collection,
                    failures=failures,
                ),
                timeout=deadline,
            )
        except TimeoutError as exc:
            failures.append(
                ResearchFailure(
                    stage="deadline",
                    target=topic_clean,
                    error_type=type(exc).__name__,
                    error=f"research exceeded its {deadline:.0f}s total budget; this report covers only what was retrieved before the deadline",
                )
            )
            logger.warning("Deep research for %r exceeded its %.0fs budget; synthesising partial evidence.", topic_clean, deadline)

        bounds: dict[str, Any] = {
            "requested_depth": depth,
            "effective_depth": effective_depth,
            "requested_max_sources": max_sources,
            "effective_max_sources": effective_max_sources,
            "requested_deadline_seconds": deadline_seconds,
            "effective_deadline_seconds": deadline,
            "include_adversarial": bool(include_adversarial),
            "lanes_planned": collection.lanes_planned,
            "max_source_chars": MAX_SOURCE_CHARS,
            "per_lane_results": LANE_RESULTS,
            "max_gap_lanes": MAX_GAP_LANES,
            "max_reported_conflicts": MAX_REPORTED_CONFLICTS,
            "clamped": [note for note in (depth_note, sources_note, deadline_note) if note],
        }
        return self._finalize(
            topic=topic_clean,
            collection=collection,
            failures=failures,
            depth=effective_depth,
            bounds=bounds,
        )

    async def conduct_research(
        self,
        topic: str,
        depth: int = 3,
        max_sources: int = 15,
        include_adversarial: bool = True,
        deadline_seconds: float = DEFAULT_RESEARCH_DEADLINE_SECONDS,
    ) -> DeepResearchReport:
        """Alias for run_research to execute end-to-end multi-hop research."""
        return await self.run_research(
            topic=topic,
            depth=depth,
            max_sources=max_sources,
            include_adversarial=include_adversarial,
            deadline_seconds=deadline_seconds,
        )

    def generate_plan(self, topic: str, depth: int = 3):
        """Compile a 5-pass search strategy plan for the target topic."""
        return FivePassSearchCompiler.compile(
            question=topic.strip(),
            domain_context=self.domain_context,
        )

    def detect_contradictions(self, sources: list[EvidenceSource]) -> list[ContradictionFinding]:
        """Return source disagreements and juxtapositions for review."""
        return self._detect_contradictions(sources)

    def identify_gaps(self, topic: str, sources: list[EvidenceSource]) -> list[ResearchGap]:
        """Public method to detect gaps in evidence collection."""
        src_map = {s.url: s for s in sources}
        return self._identify_research_gaps(topic, src_map)

    def resolve_gap(self, gap: ResearchGap) -> ResearchGap:
        """Mark an identified gap as resolved with resolution notes."""
        gap.resolved = True
        gap.resolution_notes = f"Resolved gap for subtopic '{gap.subtopic}' through targeted verification."
        return gap

    # -- collection ---------------------------------------------------------

    async def _collect(
        self,
        *,
        topic: str,
        lanes: list[Any],
        depth: int,
        max_sources: int,
        collection: _Collection,
        failures: list[ResearchFailure],
    ) -> None:
        """Run the 5-pass wave, the content wave and the bounded gap wave."""
        search_tasks = [
            self._search_lane(
                lane.query,
                lane.pass_type.value,
                max_per_lane=LANE_RESULTS,
                failures=failures,
            )
            for lane in lanes
        ]
        lane_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        for lane, res in zip(lanes, lane_results, strict=False):
            if not isinstance(res, list):
                # ``None`` means the provider failed and the failure is already
                # recorded; a BaseException here is a lane that never returned.
                if isinstance(res, BaseException):
                    failures.append(
                        ResearchFailure(
                            stage="search",
                            pass_type=lane.pass_type.value,
                            target=lane.query,
                            error_type=type(res).__name__,
                            error=str(res)[:200] or "lane raised without a message",
                        )
                    )
                continue
            collection.lanes_searched += 1
            for src in res:
                collection.discovered.setdefault(src.url, src)

        # The citation id is assigned once, here, and never reassigned: a
        # support verdict recorded against S7 must still be S7 in the report.
        for index, url in enumerate(collection.discovered, 1):
            collection.discovered[url].source_id = f"S{index}"

        candidate_urls = list(collection.discovered)[:max_sources]
        fetch_tasks = [self._fetch_and_enrich(collection.discovered[u], failures=failures) for u in candidate_urls]
        await asyncio.gather(*fetch_tasks, return_exceptions=True)

        if depth >= 2 and collection.discovered:
            gaps = self._identify_research_gaps(topic, collection.discovered)
            gap_tasks = [
                self._search_lane(
                    gap.suggested_query,
                    "gap_resolution",
                    max_per_lane=GAP_LANE_RESULTS,
                    failures=failures,
                )
                for gap in gaps[: min(depth, MAX_GAP_LANES)]
            ]
            gap_results = await asyncio.gather(*gap_tasks, return_exceptions=True)
            for gap_res in gap_results:
                if not isinstance(gap_res, list):
                    continue
                for src in gap_res:
                    if src.url in collection.discovered or len(collection.discovered) >= max_sources:
                        continue
                    collection.discovered[src.url] = src
                    src.source_id = f"S{len(collection.discovered)}"
                    await self._fetch_and_enrich(src, failures=failures)

    def _finalize(
        self,
        *,
        topic: str,
        collection: _Collection,
        failures: list[ResearchFailure],
        depth: int,
        bounds: dict[str, Any],
    ) -> DeepResearchReport:
        """Turn whatever was collected into an honestly labelled report."""
        cap = int(bounds.get("effective_max_sources") or MAX_RESEARCH_SOURCES)
        active = list(collection.discovered.values())[:cap]
        dropped = len(collection.discovered) - len(active)
        if dropped > 0:
            failures.append(
                ResearchFailure(
                    stage="truncation",
                    target=f"{dropped} discovered source(s)",
                    error_type="SourceCap",
                    error=f"discovered but never fetched: the per-run cap of {cap} sources was reached, so the report is not exhaustive",
                )
            )
        for index, src in enumerate(active, 1):
            src.source_id = f"S{index}"
        contradictions = self._detect_contradictions(active)
        coverage = _coverage(
            collection,
            failures,
            active,
            adversarial_planned=bool(bounds.get("include_adversarial")),
        )
        bounds = {**bounds, "lanes_searched": collection.lanes_searched, "sources_discovered": len(collection.discovered)}
        return self._synthesize_report(
            topic=topic,
            sources=active,
            contradictions=contradictions,
            depth=depth,
            failures=failures,
            coverage=coverage,
            bounds=bounds,
        )

    # -- providers ----------------------------------------------------------

    async def _search_lane(
        self,
        query: str,
        pass_type: str,
        max_per_lane: int = 4,
        *,
        failures: list[ResearchFailure] | None = None,
    ) -> list[EvidenceSource] | None:
        """Query search function and wrap results in EvidenceSource models.

        Returns the lane's sources, ``[]`` when the provider answered with
        nothing, or ``None`` when the provider failed. A failure is typed and
        recorded; the lane invents nothing, and the report discloses the loss.
        """
        try:
            results = await self.search_fn(query, max_per_lane)
        except Exception as exc:
            logger.warning("Search query failed for '%s': %s", query, exc)
            if failures is not None:
                failures.append(
                    ResearchFailure(
                        stage="search",
                        pass_type=pass_type,
                        target=query,
                        error_type=type(exc).__name__,
                        error=str(exc)[:200] or "provider raised without a message",
                    )
                )
            return None
        sources: list[EvidenceSource] = []
        for item in results or []:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title") or url
            snippet = item.get("snippet") or item.get("body") or ""
            if url:
                sources.append(
                    EvidenceSource(
                        url=url,
                        title=title,
                        snippet=snippet,
                        pass_type=pass_type,
                        retrieval=RETRIEVAL_SNIPPET_ONLY,
                        retrieval_note="discovered by search; the page has not been read yet",
                    )
                )
        return sources

    async def _fetch_and_enrich(self, source: EvidenceSource, *, failures: list[ResearchFailure] | None = None) -> None:
        """Fetch full webpage content and extract key metrics and findings.

        Retrieved text is bounded to :data:`MAX_SOURCE_CHARS` and any
        truncation is recorded on the source. If the page cannot be read, the
        source is downgraded to ``snippet_only`` with a note: a URL the engine
        never read must never end up carrying a page citation.
        """
        try:
            raw = await self.fetch_fn(source.url)
        except Exception as exc:
            logger.debug("Failed to fetch content from %s: %s", source.url, exc)
            if failures is not None:
                failures.append(
                    ResearchFailure(
                        stage="fetch",
                        pass_type=source.pass_type,
                        target=source.url,
                        error_type=type(exc).__name__,
                        error=str(exc)[:200] or "provider raised without a message",
                    )
                )
            source.retrieval = RETRIEVAL_SNIPPET_ONLY
            source.retrieval_note = f"page body could not be retrieved ({type(exc).__name__}); only the search-provider snippet is available"
            source.content = source.snippet
            source.content_truncated = False
            source.content_chars_original = 0
            source.key_findings = [source.snippet] if source.snippet else []
            return

        text = raw or ""
        source.content_chars_original = len(text)
        source.content_truncated = len(text) > MAX_SOURCE_CHARS
        if source.content_truncated:
            logger.info("Truncated retrieved content from %s to %d chars (was %d).", source.url, MAX_SOURCE_CHARS, len(text))
        source.content = text[:MAX_SOURCE_CHARS] or source.snippet
        if not source.content.strip():
            source.retrieval = RETRIEVAL_SNIPPET_ONLY
            source.retrieval_note = "the page returned no readable text; only the search-provider snippet is available"
            source.key_findings = [source.snippet] if source.snippet else []
            return

        # The body is in hand. Only a quarantine below can take it away again.
        source.retrieval = RETRIEVAL_PAGE
        source.retrieval_note = (
            f"page body retrieved and screened ({len(source.content)} characters"
            + (f", truncated from {source.content_chars_original}" if source.content_truncated else "")
            + ")"
        )

        # Fetched text is untrusted data. If it is trying to redirect the
        # agent, fall back to the search snippet (which came from the search
        # provider, not the page) and record why. No verdict -> no change.
        await self._screen_for_injection(source, failures=failures)

        # Extract metrics / stats patterns
        metrics: dict[str, str] = {}
        # Match percentages
        pcts = re.findall(r"(\b\d+(?:\.\d+)?%\b[^\.\n;]{0,40})", source.content)
        for i, p in enumerate(pcts[:3]):
            metrics[f"metric_{i + 1}"] = p.strip()

        # Match speedups / multipliers
        multipliers = re.findall(r"(\b\d+(?:\.\d+)?x\b[^\.\n;]{0,40})", source.content)
        for i, m in enumerate(multipliers[:2]):
            metrics[f"multiplier_{i + 1}"] = m.strip()

        source.extracted_metrics = metrics

        # Extract bullet findings
        findings: list[str] = []
        lines = [line.strip() for line in source.content.split("\n") if len(line.strip()) > 30]
        for line in lines[:4]:
            if not line.startswith("#"):
                findings.append(line[:160])
        source.key_findings = findings or [source.snippet]

        # A line extracted from a page is not the same as a page that says it.
        # Check each finding against the content it came from.
        await self._verify_findings(source)

    async def _screen_for_injection(self, source: EvidenceSource, *, failures: list[ResearchFailure] | None = None) -> None:
        """Flag — and neutralise — prompt injection in fetched page content.

        The page body is what an attacker controls. Replacing it with the
        provider snippet keeps the source usable while removing the payload.
        ``None`` from the scanner means "no verdict", which is never treated as
        clean: the content stays, and the risk field stays None.

        The whole bounded body is scanned, not just its first 12k characters:
        the scanner's own default cap is smaller than the fetcher's limit, so a
        payload parked past that offset would otherwise reach the report
        unscreened. The untrusted body is never turned into instructions, only
        quoted as data, and every quote is labelled with the source it came from.
        """
        if not (source.content or "").strip():
            return
        try:
            from alpha.security.injection import scan_content

            verdict = await scan_content(source.content, source=source.url, max_chars=max(1, len(source.content)))
        except Exception:
            logger.debug("Injection screen unavailable for %s; source unchanged.", source.url)
            return
        if verdict is None:
            return
        source.injection_risk = verdict.risk
        source.injection_signals = list(verdict.fired)
        if verdict.is_injection and verdict.risk >= INJECTION_QUARANTINE_AT:
            logger.warning(
                "Quarantined fetched content from %s (injection risk %.2f, signals=%s); falling back to the snippet.",
                source.url,
                verdict.risk,
                verdict.fired,
            )
            if failures is not None:
                failures.append(
                    ResearchFailure(
                        stage="injection_quarantine",
                        pass_type=source.pass_type,
                        target=source.url,
                        error_type="PromptInjection",
                        error=f"retrieved page body was quarantined by the prompt-injection screen (risk {verdict.risk:.2f}, signals {list(verdict.fired)}); the body was discarded and only the search snippet is used",
                    )
                )
            source.retrieval = RETRIEVAL_SNIPPET_ONLY
            source.retrieval_note = f"page body was quarantined by the prompt-injection screen (risk {verdict.risk:.2f}, signals: {', '.join(verdict.fired) or 'unspecified'}); it was discarded and only the search snippet is used"
            source.content = source.snippet
            source.content_truncated = False
            source.key_findings = [source.snippet] if source.snippet else []

    async def _verify_findings(self, source: EvidenceSource) -> None:
        """Check each extracted finding against the content it came from.

        Extraction is line-shaped: any sentence over 30 characters becomes a
        "key finding", including ones that merely sit near the real claim. This
        asks System One whether the page actually shows each one.

        Contradicted findings are dropped — carrying a claim the source
        refutes into a report is worse than carrying nothing. Unsupported ones
        are kept but flagged, and the source loses confidence, because
        "unsupported" is often "this extractor picked a bad line" rather than
        "the page is wrong".

        Findings System One has no verdict on are left exactly as they are, and
        the source is labelled ``unverified`` so a reader can tell an unchecked
        extraction from a checked one.
        """
        findings = [f for f in source.key_findings if f and f.strip()]
        if not findings or not (source.content or "").strip():
            source.citation_status = "unverified"
            return
        if not source.page_retrieved:
            # Checking a search snippet against the same snippet proves nothing,
            # and a quarantined page must never be reported as a verified
            # source just because its replacement text matched itself.
            source.citation_status = "unverified"
            return
        try:
            from alpha.agents.middlewares.citation_support import (
                CONTRADICTED,
                SUPPORTED,
                UNSUPPORTED,
                judge_batch,
            )

            checked_findings = findings[:MAX_VERIFIED_FINDINGS]
            pairs = [(source.source_id, finding, source.content) for finding in checked_findings]
            verdicts = await judge_batch(pairs)
        except Exception:
            logger.debug("Citation support unavailable for %s; findings unchanged.", source.url)
            source.citation_status = "unverified"
            return
        if not verdicts:
            source.citation_status = "unverified"
            return

        contradicted = {v.claim for v in verdicts if v.verdict == CONTRADICTED}
        unsupported = {v.claim for v in verdicts if v.verdict == UNSUPPORTED}
        supported = {v.claim for v in verdicts if v.verdict == SUPPORTED}
        if contradicted:
            logger.info(
                "Dropped %d contradicted finding(s) from %s",
                len(contradicted),
                source.url,
            )
            source.key_findings = [f for f in source.key_findings if f not in contradicted]
            source.extracted_facts = [f for f in source.extracted_facts if f not in contradicted]
        if unsupported:
            source.unsupported_findings = sorted(unsupported)
        if contradicted or unsupported:
            penalty = 0.15 * (len(contradicted) + len(unsupported))
            source.confidence = round(max(0.10, source.confidence - penalty), 3)
            source.citation_status = "unsupported"
        elif supported and not (unsupported or contradicted) and set(checked_findings).issubset(supported):
            source.citation_status = "verified"
        else:
            source.citation_status = "unverified"

    def _identify_research_gaps(self, topic: str, sources: dict[str, EvidenceSource]) -> list[ResearchGap]:
        """Detect gaps in the current evidence collection."""
        gaps: list[ResearchGap] = []
        has_metrics = any(bool(s.extracted_metrics) for s in sources.values())
        has_adversarial = any(s.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION.value for s in sources.values())

        if not has_metrics:
            gaps.append(
                ResearchGap(
                    subtopic="empirical benchmarks",
                    rationale="No concrete performance metrics or percentages collected yet.",
                    suggested_query=f"{topic} performance benchmark empirical statistics",
                    priority=1,
                )
            )

        if not has_adversarial:
            gaps.append(
                ResearchGap(
                    subtopic="limitations and criticisms",
                    rationale="Lacking critical counter-arguments and failure modes.",
                    suggested_query=f"{topic} problems limitations criticism edge cases",
                    priority=2,
                )
            )

        # Domain trade-offs
        gaps.append(
            ResearchGap(
                subtopic="architectural trade-offs",
                rationale="Need deep comparative perspective with existing alternatives.",
                suggested_query=f"{topic} comparison architectural trade-offs production lessons",
                priority=3,
            )
        )
        return gaps

    def _detect_contradictions(self, sources: list[EvidenceSource]) -> list[ContradictionFinding]:
        """Surface disagreements between retrieved sources, and quote both sides.

        Every adversarial source is compared against every supporting source —
        not just the first of each — so a conflict is not missed because it
        happened to sit at index 1. A pair is reported as a
        ``stated_conflict`` when the two sources disagree about the same
        measurable subject and the polarity markers fire on exactly one side;
        both statements are quoted. When adversarial and supporting evidence
        exist but nothing conflicts, one honestly-worded ``juxtaposition``
        entry is emitted so the review is still possible. Neither kind asserts
        who is right.
        """
        if len(sources) < 2:
            return []
        adv_sources = [s for s in sources if s.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION.value]
        std_sources = [s for s in sources if s.pass_type != SearchPassType.ADVERSARIAL_CONTRADICTION.value]
        if not adv_sources or not std_sources:
            return []

        findings: list[ContradictionFinding] = []
        for s_con in adv_sources:
            for s_pro in std_sources:
                if len(findings) >= MAX_REPORTED_CONFLICTS:
                    break
                con_claims = _claims_of(s_con)
                pro_claims = _claims_of(s_pro)
                match: tuple[str, str] | None = None
                pro_evidence = con_evidence = ""
                for con_claim in con_claims:
                    for pro_claim in pro_claims:
                        found = _pick_conflict(pro_claim, con_claim)
                        if found:
                            match = found
                            pro_evidence, con_evidence = pro_claim, con_claim
                            break
                    if match:
                        break
                if not match:
                    continue
                subject, marker = match
                findings.append(
                    ContradictionFinding(
                        claim=f"Sources disagree about '{subject}': one asserts it, the other denies it",
                        kind=CONFLICT,
                        subject=subject,
                        source_a_title=s_pro.title,
                        source_a_url=s_pro.url,
                        source_b_title=s_con.title,
                        source_b_url=s_con.url,
                        evidence_a=pro_evidence[:240],
                        evidence_b=con_evidence[:240],
                        adversarial_evidence=s_con.snippet or s_con.content,
                        nuance_explanation=(
                            f"Supporting source asserts: {pro_evidence[:240]} Adversarial source denies it (marker: {marker!r}): {con_evidence[:240]} "
                            f"Both statements are quoted from retrieved content. This flags a disagreement for human review; it does not establish which source is correct."
                        ),
                    )
                )

        if findings:
            return findings[:MAX_REPORTED_CONFLICTS]

        s_pro = std_sources[0]
        s_con = adv_sources[0]
        support_excerpt = (s_pro.snippet or s_pro.content or "No extractable excerpt")[:240]
        adversarial_excerpt = (s_con.snippet or s_con.content or "No extractable excerpt")[:240]
        return [
            ContradictionFinding(
                claim="Supporting and adversarial sources were retrieved side by side; no direct disagreement was detected between them",
                kind=JUXTAPOSITION,
                source_a_title=s_pro.title,
                source_a_url=s_pro.url,
                source_b_title=s_con.title,
                source_b_url=s_con.url,
                evidence_a=support_excerpt,
                evidence_b=adversarial_excerpt,
                adversarial_evidence=s_con.snippet or s_con.content,
                nuance_explanation=(
                    f"Supporting evidence excerpt: {support_excerpt} Adversarial evidence excerpt: {adversarial_excerpt} "
                    f"These sources are juxtaposed for review; this pass does not establish a logical contradiction."
                ),
            )
        ]

    # -- synthesis ----------------------------------------------------------

    def _synthesize_report(
        self,
        topic: str,
        sources: list[EvidenceSource],
        contradictions: list[ContradictionFinding],
        depth: int = 3,
        *,
        failures: list[ResearchFailure] | None = None,
        coverage: str = "unknown",
        bounds: dict[str, Any] | None = None,
    ) -> DeepResearchReport:
        """Synthesize only claims backed by content the engine actually holds."""
        failures = list(failures or [])
        bounds = dict(bounds or {})

        if not sources:
            executive_summary = f"No verifiable sources were retrieved for **{topic}**. No research claims were generated."
            coverage_lines = self._coverage_lines(topic, sources, failures, coverage, bounds)
            markdown_content = "\n".join(
                [
                    f"# Deep Research Report: {topic}",
                    "",
                    "> **Status:** No evidence retrieved",
                    "",
                    "## Executive Summary",
                    "",
                    executive_summary,
                    "",
                    "## Coverage, Provenance and Failures",
                    "",
                    *coverage_lines,
                    "",
                    "## Next Steps",
                    "",
                    "- Retry with a narrower query or a different approved source family.",
                    "- Verify provider availability with `agent_eye_sources` and inspect backend errors.",
                    "- Do not treat this report as factual evidence.",
                    "",
                ]
            )
            return DeepResearchReport(
                topic=topic,
                executive_summary=executive_summary,
                core_findings=[],
                comparative_analysis="No evidence matrix is available because no sources were retrieved.",
                adversarial_findings="No evidence-backed adversarial analysis is available.",
                contradictions=[],
                sources=[],
                citations=[],
                markdown_content=markdown_content,
                status="no_evidence",
                depth=depth,
                failures=failures,
                coverage="none",
                bounds=bounds,
            )

        for i, s in enumerate(sources, 1):
            s.source_id = f"S{i}"

        # Citations carry the id they are cited by and what was really read, so
        # a consumer can join a claim to the exact text it came from.
        citations: list[dict[str, str]] = [
            {
                "source_id": s.source_id,
                "title": s.title,
                "url": s.url,
                "domain": s.domain,
                "retrieval": s.retrieval,
                "citation_status": s.citation_status,
            }
            for s in sources
        ]

        # 1. Core Findings -- a page citation only where the page was read.
        core_findings: list[str] = []
        snippet_findings: list[str] = []
        for s in sources:
            for f in s.key_findings[:2]:
                if not f or len(f) <= 20:
                    continue
                claim = self._claim_line(s, f)
                if not s.page_retrieved:
                    snippet_findings.append(claim)
                if claim not in core_findings:
                    core_findings.append(claim)

        if not core_findings:
            core_findings = ["No extractable evidence-backed findings were returned by the selected sources."]

        # 2. Executive summary — derived from what actually happened.
        exec_summary = self._executive_summary(
            topic=topic,
            sources=sources,
            core_findings=core_findings,
            snippet_findings=snippet_findings,
            bounds=bounds,
            coverage=coverage,
        )

        # 3. Comparative Table
        comp_rows: list[str] = []
        for s in sources[:6]:
            sample_metric = next(iter(s.extracted_metrics.values()), "Documented standard")
            comp_rows.append(f"| {s.domain} | [{s.source_id}: {s.title}]({s.url}) | `{s.pass_type}` | `{s.citation_status}` | `{s.retrieval}` | {sample_metric} |")

        comp_table = (
            "| Source Domain | Document / Artifact | Research Facet | Citation Status | Provenance | Key Metric / Highlight |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n" + "\n".join(comp_rows)
        )

        # 4. Adversarial Findings -- same provenance rule as the key findings.
        adv_texts: list[str] = []
        for s in sources:
            if s.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION.value or "adversarial" in s.pass_type:
                for f in s.key_findings[:2]:
                    adv_texts.append(f"- **Risk / Constraint**: {self._claim_line(s, f)}")

        if not adv_texts:
            adv_texts.append("- No evidence-backed adversarial findings were returned by the selected sources.")

        adversarial_section = "\n".join(adv_texts)

        # 5. Build Full Markdown Content
        md_lines = [
            f"# Deep Research Report: {topic}",
            "",
            "> **Autonomous Research Brief** | Synthesized by Alpha Deep Research Superintelligence",
            f"> *Date:* {datetime.now(UTC).strftime('%B %d, %Y')} | *Gathered Sources:* {len(sources)} | *Methodology:* 5-Pass Multi-Lane Search",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            exec_summary,
            "",
            "## Key Findings & Empirical Evidence",
            "",
        ]

        for finding in core_findings:
            md_lines.append(f"- {finding}")

        md_lines.extend(
            [
                "",
                "## Gathered Sources & Evidence Matrix",
                "",
                comp_table,
                "",
                "## Adversarial Analysis & Boundary Conditions",
                "",
                "A robust research investigation must account for failure modes, performance cliffs, and trade-offs:",
                "",
                adversarial_section,
                "",
            ]
        )

        if contradictions:
            conflicts = [c for c in contradictions if c.is_conflict]
            md_lines.extend(
                [
                    "## Adversarial Source Comparison",
                    "",
                    f"{len(conflicts)} source disagreement(s) detected out of {len(contradictions)} comparison(s). "
                    "This section quotes both sides for human/model review; it does not assert a proven logical contradiction or decide which source is correct.",
                    "",
                ]
            )
            for c in contradictions:
                md_lines.append(f"### [{c.kind}] {c.claim}")
                md_lines.append(f"- **Supporting source**: [{c.source_a_title}]({c.source_a_url})")
                md_lines.append(f"- **Adversarial source**: [{c.source_b_title}]({c.source_b_url})")
                if c.evidence_a:
                    md_lines.append(f"- **Supporting evidence (quoted)**: {c.evidence_a}")
                if c.evidence_b:
                    md_lines.append(f"- **Adversarial evidence (quoted)**: {c.evidence_b}")
                md_lines.append(f"- **Evidence comparison**: {c.nuance_explanation}")
                md_lines.append("")

        md_lines.extend(
            [
                "## Coverage, Provenance and Failures",
                "",
                *self._coverage_lines(topic, sources, failures, coverage, bounds),
                "",
                "## Sources & Evidence Bibliography",
                "",
            ]
        )

        for i, s in enumerate(sources, 1):
            provenance = f"*Provenance:* `{s.retrieval}` — {s.retrieval_note}"
            md_lines.append(
                f"{i}. **[{s.source_id}] [{s.title}]({s.url})**  \n"
                f"   *Domain:* `{s.domain}` | *Facet:* `{s.pass_type}` | *Citation status:* `{s.citation_status}`  \n"
                f"   {provenance}  \n"
                f"   *{s.snippet[:140]}...*"
            )

        md_lines.extend(
            [
                "",
                "---",
                "*Source support status is reported explicitly; a source is labeled verified only after semantic support checks. "
                "A page citation is issued only for a page whose body was actually retrieved; anything known only from a search snippet is labelled as such.*",
            ]
        )

        full_markdown = "\n".join(md_lines)

        return DeepResearchReport(
            topic=topic,
            executive_summary=exec_summary,
            core_findings=core_findings,
            comparative_analysis=comp_table,
            adversarial_findings=adversarial_section,
            contradictions=contradictions,
            sources=sources,
            citations=citations,
            markdown_content=full_markdown,
            status="completed",
            depth=depth,
            failures=failures,
            coverage=coverage,
            bounds=bounds,
        )

    @staticmethod
    def _claim_line(source: EvidenceSource, finding: str) -> str:
        """Render one claim with a citation only when the page was really read.

        A ``[[Sn]](url)`` marker is the report's promise that the engine holds
        the text at that URL. A source that is ``snippet_only`` must therefore
        never receive one: its statement is the search provider's snippet, and it
        is labelled as such in place of the citation.
        """
        if source.page_retrieved:
            return f"{finding} [[{source.source_id}]]({source.url})"
        return f"{finding} _(search snippet only — `{source.url}` was discovered but its page was not read; not page evidence)_"

    @staticmethod
    def _executive_summary(
        *,
        topic: str,
        sources: list[EvidenceSource],
        core_findings: list[str],
        snippet_findings: list[str],
        bounds: dict[str, Any],
        coverage: str,
    ) -> str:
        """Describe the evidence that exists — never evidence that does not."""
        pages = sum(1 for s in sources if s.page_retrieved)
        snippets = len(sources) - pages
        facets = sorted({s.pass_type for s in sources})
        metric_sources = sum(1 for s in sources if s.extracted_metrics)
        adversarial = [s for s in sources if s.pass_type == SearchPassType.ADVERSARIAL_CONTRADICTION.value or "adversarial" in s.pass_type]
        planned = bounds.get("lanes_planned")
        searched = bounds.get("lanes_searched", planned)

        sentences = [
            f"Deep research on **{topic}** had {searched} of {planned} planned search lanes return; "
            f"the sources it gathered come from {len(facets)} research facet(s): " + ", ".join(f"`{f}`" for f in facets) + "."
        ]
        if pages:
            sentences.append(f"{pages} source page(s) were retrieved and read; their text is quoted below and each quote carries its source id.")
        if snippets:
            sentences.append(f"{snippets} candidate(s) are known only from a search snippet and are marked as such throughout; they are not page evidence.")
        if metric_sources:
            sentences.append(f"Concrete metrics were extracted from {metric_sources} source(s).")
        else:
            sentences.append("No concrete metrics could be extracted from the retrieved pages.")
        if adversarial:
            sentences.append(f"{len(adversarial)} source(s) came from the adversarial/falsification lane, so critical evidence is represented.")
        else:
            sentences.append("No adversarial or falsification source was retrieved, so this report carries no critical-evidence coverage.")
        sentences.append(f"The Key Findings section carries {len(core_findings)} statement(s), of which {len(snippet_findings)} rest on an unread page. Overall coverage for this run: {coverage}.")
        return " ".join(sentences)

    @staticmethod
    def _coverage_lines(
        topic: str,
        sources: list[EvidenceSource],
        failures: list[ResearchFailure],
        coverage: str,
        bounds: dict[str, Any],
    ) -> list[str]:
        """Render the coverage ledger: what was read, what failed, what was clamped."""
        pages = sum(1 for s in sources if s.page_retrieved)
        snippets = len(sources) - pages
        lines = [f"- **Coverage:** `{coverage}` for `{topic}`."]
        lines.append(
            f"- **Lanes:** {bounds.get('lanes_searched', '?')} of {bounds.get('lanes_planned', '?')} planned search lanes returned; "
            f"{bounds.get('sources_discovered', len(sources))} candidate source(s) discovered."
        )
        lines.append(f"- **Pages read:** {pages} (content read in full) | **snippet-only:** {snippets} (discovered, page not read).")
        truncated = [s for s in sources if s.content_truncated]
        if truncated:
            lines.append(
                f"- **Truncated retrieved content:** {len(truncated)} source(s) exceeded the {MAX_SOURCE_CHARS}-character per-source cap "
                f"and were cut short ({', '.join(s.source_id for s in truncated)}); their findings come from the retained prefix only."
            )
        applied = [
            f"depth={bounds.get('effective_depth', '?')}",
            f"max_sources={bounds.get('effective_max_sources', '?')}",
            f"total budget={bounds.get('effective_deadline_seconds', '?')}s",
            f"per-source content cap={MAX_SOURCE_CHARS} chars",
            f"per-lane results={bounds.get('per_lane_results', '?')}",
        ]
        clamped = bounds.get("clamped") or []
        clamp_text = f" — clamped: {'; '.join(clamped)}" if clamped else ""
        lines.append(f"- **Bounds applied:** {', '.join(applied)}{clamp_text}.")
        if failures:
            lines.append(f"- **Disclosed failures ({len(failures)}):** this report does not cover these.")
            lines.extend(failure.to_line() for failure in failures)
        else:
            lines.append("- **Disclosed failures:** none — every planned lane ran and every selected page was retrieved.")
        return lines
