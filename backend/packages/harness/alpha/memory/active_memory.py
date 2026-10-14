"""Active Memory Two-Tier Escalation Engine.

Tier 1 is a deterministic lexical pass over curated memory lines; Tier 2 is a
caller-supplied deeper pass. Three properties are load-bearing and were fixed
after an audit of the original implementation:

* **The memory root is pinned, never ``Path.cwd()``.** A Gateway whose working
  directory changes must not start reading a different ``MEMORY.md``. With no
  explicit files the store resolves under the agent runtime home.
* **A retrieval score is not a truth confidence.** ``MemoryLookupResult.confidence``
  is how well the text matched the query. Tier 2 no longer reports a fixed
  ``0.90`` as though a provider completing a call made its answer likely to be
  correct; a plain-text handler answer is labelled ``unverified`` and its
  relevance is left at ``0.0`` until the handler supplies real evidence.
* **Escalation is never silently skipped inside a running event loop.** The
  sync ``query()`` cannot run ``asyncio.run()`` from a live loop, so it used to
  return without escalating and report a Tier-1 answer as if it were complete.
  :meth:`ActiveMemoryRouter.query_async` is the supported path there.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from alpha.memory.dreaming.store import get_dream_store

logger = logging.getLogger(__name__)

#: Call-site label recorded in the System One decision log (see evaluation/system_one_calibration.py).
SITE = "memory_gate"

#: Longest query the router will score. Beyond this the lexical pass is not the
#: right tool, and silently scoring a huge blob produces confident-looking noise.
MAX_QUERY_CHARS = 4_000

#: Cap on memory lines loaded for one query, so an unbounded MEMORY.md cannot
#: turn a lookup into an unbounded scan.
MAX_MEMORY_LINES = 5_000


@dataclass(frozen=True)
class MemoryCandidate:
    """One Tier-1 hit plus where it came from.

    ``relevance`` is lexical overlap in ``[0, 1]``. It is deliberately named
    apart from a truth confidence: matching a line well says nothing about
    whether the line is correct.
    """

    text: str
    relevance: float
    source: str = ""
    line_number: int = 0


@dataclass
class MemoryLookupResult:
    """Outcome of active memory retrieval."""

    query: str
    tier: int  # 1 (deterministic) or 2 (escalation)
    matches: list[str] = field(default_factory=list)
    #: Lexical/retrieval relevance in ``[0, 1]``. NOT a probability of truth.
    confidence: float = 0.0
    escalated: bool = False
    reason: str = ""
    #: Structured Tier-1 hits with source locations, when any were found.
    candidates: tuple[MemoryCandidate, ...] = ()
    #: Truth posture: ``unverified`` for anything not backed by real evidence.
    evidence_state: str = "unverified"


class ActiveMemoryRouter:
    """Two-tier memory retrieval router combining instant local lookup with deep escalation."""

    def __init__(
        self,
        memory_files: list[Path] | None = None,
        escalation_threshold: float = 0.60,
        *,
        memory_root: Path | str | None = None,
        max_query_chars: int = MAX_QUERY_CHARS,
        max_memory_lines: int = MAX_MEMORY_LINES,
    ):
        self.memory_files = memory_files or []
        self.escalation_threshold = escalation_threshold
        #: Explicit runtime root for the DreamStore fallback. None resolves the
        #: agent runtime home -- never the process working directory.
        self.memory_root = Path(memory_root) if memory_root is not None else None
        self.max_query_chars = int(max_query_chars)
        self.max_memory_lines = int(max_memory_lines)

    def _system_one_escalation_gate(
        self,
        query_text: str,
        lines: list[str],
        top_matches: list[str],
        top_score: float,
    ) -> bool | None:
        """Decide whether Tier-2 escalation is worth its cost.

        Returns True (escalate), False (Tier 1 answer is enough), or None
        (no confident opinion -- caller keeps its threshold behaviour).

        Tier 2 is a full LLM call, so skipping it when the local hit already
        answers the question is the highest-value place to put a 100ms
        decision. A confident "not needed" saves seconds and tokens.
        """
        try:
            from alpha.models.system_one import BooleanQuestion, get_system_one_client

            client = get_system_one_client()
            cfg = client.config
            if not cfg.enabled or not cfg.enable_memory_escalation or not client.is_available():
                return None

            async def _query() -> bool | None:
                result = await client.evaluate(
                    {
                        "query": query_text,
                        "top_local_matches": top_matches[:5],
                        "local_match_score": round(top_score, 3),
                        "memory_excerpt": lines[:60],
                    },
                    {
                        "needs_deep_retrieval": BooleanQuestion(
                            instructions=(
                                "Do `top_local_matches` already answer `query` well enough that "
                                "escalating to an expensive deep-reasoning retrieval pass would be wasteful? "
                                "Answer yes if the local matches already answer it; no if deeper retrieval is genuinely needed."
                            ),
                            criteria={
                                "true": "The local matches already answer the query adequately.",
                                "false": "The local matches miss the answer; deeper retrieval is needed.",
                            },
                        ),
                    },
                    site=SITE,
                )
                if result is None:
                    return None
                answer = result.get("needs_deep_retrieval")
                if answer is None or not answer.meets(cfg.min_confidence):
                    return None
                answered_by_local = float(answer.value) >= 0.5
                return not answered_by_local  # local answered -> do not escalate

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_query())
            return None  # never block a live event loop
        except Exception:
            logger.debug("System One memory escalation gate unavailable; using threshold.", exc_info=True)
            return None

    def _resolve_store(self):
        """Build the DreamStore at a deterministic, runtime-home-pinned root.

        ``DreamStore``'s own default is ``Path.cwd()``. Passing an explicit root
        is what keeps a working-directory change from silently repointing
        durable memory at a different file.
        """
        from alpha.memory.dreaming.store import DreamStore

        if self.memory_root is not None:
            return DreamStore(self.memory_root)
        try:
            from alpha.config.runtime_paths import runtime_home

            return DreamStore(runtime_home() / "memory")
        except Exception:  # noqa: BLE001 - a missing runtime home falls back, loudly at debug
            logger.debug("runtime home unavailable for active memory; falling back to process store", exc_info=True)
            return get_dream_store()

    def _collect_memory_lines(self) -> list[tuple[str, int, str]]:
        """Return ``(text, line_number, source)`` triples, bounded in size."""
        lines: list[tuple[str, int, str]] = []
        # If no explicit files passed, pull from DreamStore
        if not self.memory_files:
            store = self._resolve_store()
            try:
                mem_text = store.read_memory()
                source = str(store.memory_path)
            except Exception:  # noqa: BLE001 - an unreadable store is empty, and says so
                logger.debug("dream store unreadable for active memory lookup", exc_info=True)
                return []
            for line_no, raw in enumerate(mem_text.splitlines(), start=1):
                stripped = raw.strip()
                if stripped:
                    lines.append((stripped, line_no, source))
        else:
            for path in self.memory_files:
                if path.exists():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    for line_no, raw in enumerate(text.splitlines(), start=1):
                        stripped = raw.strip()
                        if stripped:
                            lines.append((stripped, line_no, str(path)))
        return lines[: self.max_memory_lines]

    def _score(
        self,
        query_text: str,
        entries: list[tuple[str, int, str]],
    ) -> tuple[list[MemoryCandidate], float]:
        """Tier-1 deterministic lexical scoring, producing source-bearing hits."""
        tokens = [t.lower() for t in re.findall(r"\w+", query_text) if len(t) > 2]
        scored: list[tuple[float, MemoryCandidate]] = []

        for line, line_no, source in entries:
            if line.startswith("#"):
                continue  # Skip headers
            if not tokens:
                continue
            line_lower = line.lower()
            matches_count = sum(1 for token in tokens if token in line_lower)
            score = matches_count / len(tokens)
            if score > 0:
                scored.append(
                    (
                        score,
                        MemoryCandidate(
                            text=line,
                            relevance=score,
                            source=source,
                            line_number=line_no,
                        ),
                    )
                )

        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0] if scored else 0.0
        candidates = tuple(candidate for _score, candidate in scored[:3])
        return candidates, top_score

    def query(
        self,
        query_text: str,
        escalation_handler: Callable[[str, list[str]], str] | None = None,
    ) -> MemoryLookupResult:
        """Route query through Tier 1 deterministic lookup, escalating to Tier 2 if needed.

        Synchronous callers only. Inside a running event loop this cannot run a
        blocking escalation, so use :meth:`query_async` there -- the sync path
        reports the loop condition instead of pretending Tier 2 ran.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            in_loop = False
        else:
            in_loop = True
            if escalation_handler is not None:
                logger.debug("ActiveMemoryRouter.query() called from a running event loop; Tier-2 escalation is not attempted. Use query_async() for escalation inside async code.")
        return self._query_sync(query_text, escalation_handler, _in_loop=in_loop)

    def _query_sync(
        self,
        query_text: str,
        escalation_handler: Callable[[str, list[str]], str] | None,
        *,
        _in_loop: bool,
    ) -> MemoryLookupResult:
        query_text = (query_text or "").strip()
        if len(query_text) > self.max_query_chars:
            query_text = query_text[: self.max_query_chars]

        entries = self._collect_memory_lines()
        candidates, top_score = self._score(query_text, entries)
        top_matches = [candidate.text for candidate in candidates]

        if not entries:
            if escalation_handler:
                ans = escalation_handler(query_text, [])
                return MemoryLookupResult(
                    query=query_text,
                    tier=2,
                    matches=[ans],
                    # A completed provider call is not a correct answer. The
                    # handler returned prose with no evidence attached, so the
                    # answer is offered unverified with no relevance claim.
                    confidence=0.0,
                    escalated=True,
                    reason="No local memory files found; escalated to Tier 2.",
                    evidence_state="unverified",
                )
            return MemoryLookupResult(
                query=query_text,
                tier=1,
                matches=[],
                confidence=0.0,
                escalated=False,
                reason="Empty memory store.",
                evidence_state="unverified",
            )

        # Tier 1: Deterministic Keyword / Substring Scoring
        if top_score >= self.escalation_threshold and top_matches:
            return MemoryLookupResult(
                query=query_text,
                tier=1,
                matches=top_matches,
                confidence=top_score,
                escalated=False,
                reason=f"Resolved deterministically via Tier 1 with confidence {top_score:.2f}.",
                candidates=candidates,
                evidence_state="unverified",
            )

        # Tier 1.5: System One (Jev) gate -- skip the expensive Tier-2 LLM call
        # when the local matches already answer the query.
        if escalation_handler is not None and not _in_loop:
            gate = self._system_one_escalation_gate(query_text, [entry[0] for entry in entries], top_matches, top_score)
            if gate is False:
                return MemoryLookupResult(
                    query=query_text,
                    tier=1,
                    matches=top_matches,
                    confidence=max(top_score, self.escalation_threshold),
                    escalated=False,
                    reason=f"System One judged Tier 1 sufficient (score {top_score:.2f}); deep retrieval skipped.",
                    candidates=candidates,
                    evidence_state="unverified",
                )

        # Tier 2: Escalation Check
        if escalation_handler is not None:
            if _in_loop:
                # Do not claim an escalation that cannot run here. Returning the
                # Tier-1 hits with an explicit reason is honest; the previous
                # behaviour silently fell through and reported a Tier-1 answer
                # as if the query had been fully resolved.
                return MemoryLookupResult(
                    query=query_text,
                    tier=1,
                    matches=top_matches,
                    confidence=top_score,
                    escalated=False,
                    reason=(f"Tier 1 confidence ({top_score:.2f}) below threshold ({self.escalation_threshold}), but Tier 2 escalation cannot run on a live event loop; call query_async(). Tier 1 hits returned unescalated."),
                    candidates=candidates,
                    evidence_state="unverified",
                )
            escalation_output = escalation_handler(query_text, [entry[0] for entry in entries])
            return MemoryLookupResult(
                query=query_text,
                tier=2,
                matches=[escalation_output],
                # Deliberately 0.0, not the previous fixed 0.90: the handler
                # returned text, not evidence, and a provider completing a call
                # says nothing about whether the answer is correct.
                confidence=0.0,
                escalated=True,
                reason=f"Tier 1 confidence ({top_score:.2f}) below threshold ({self.escalation_threshold}); escalated to Tier 2 deep reasoning.",
                candidates=candidates,
                evidence_state="unverified",
            )

        # Fallback to whatever Tier 1 found if no escalation handler
        return MemoryLookupResult(
            query=query_text,
            tier=1,
            matches=top_matches,
            confidence=top_score,
            escalated=False,
            reason="Tier 1 confidence low, but no Tier 2 escalation handler registered.",
            candidates=candidates,
            evidence_state="unverified",
        )

    async def query_async(
        self,
        query_text: str,
        escalation_handler: Callable[[str, list[str]], str] | None = None,
    ) -> MemoryLookupResult:
        """Async entry point: Tier 1 off-loop, escalation awaited when needed.

        This is the supported route from a live event loop. It never attempts
        ``asyncio.run()`` from inside a running loop, so an escalation is either
        performed or reported as not performed -- never silently dropped.
        """
        loop = asyncio.get_running_loop()
        base = await loop.run_in_executor(None, functools.partial(self._query_sync, query_text, None, _in_loop=True))

        needs_escalation = escalation_handler is not None and not base.escalated and (base.confidence < self.escalation_threshold or not base.matches)
        if not needs_escalation:
            return base

        lines = [entry[0] for entry in self._collect_memory_lines()]
        gate = await loop.run_in_executor(
            None,
            functools.partial(self._system_one_escalation_gate, query_text, lines, list(base.matches), base.confidence),
        )
        if gate is False:
            return base

        output = await loop.run_in_executor(None, escalation_handler, query_text, lines)
        return MemoryLookupResult(
            query=query_text,
            tier=2,
            matches=[output],
            confidence=0.0,
            escalated=True,
            reason=(f"Tier 1 confidence ({base.confidence:.2f}) below threshold ({self.escalation_threshold}); escalated to Tier 2 deep reasoning."),
            candidates=base.candidates,
            evidence_state="unverified",
        )


_global_memory_router = ActiveMemoryRouter()


def get_active_memory_router() -> ActiveMemoryRouter:
    return _global_memory_router
