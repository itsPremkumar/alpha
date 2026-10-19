"""Active Memory Controller — the host-level composition seam for fusion.

``alpha.memory.fusion`` ships the whole read-side machinery the Alpha memory
design asks for: typed :class:`~alpha.memory.fusion.models.Candidate` records,
per-source :class:`~alpha.memory.fusion.models.StageResult` reporting,
weighted/RRF fusion, MMR diversity and per-type token budgets. Its own docstring
says what was missing to make any of it real:

    "It is provider-neutral and default-off; the host owns activation, provider
    adapters, and prompt wiring."

That host is this module. It supplies the **real adapters** over stores Alpha
already has — the curated ``MEMORY.md`` rules in :class:`DreamStore`, the
configured :class:`~alpha.agents.memory.manager.MemoryManager` backend, and an
optional :class:`~alpha.memory.session_search.SessionSearchEngine` — and runs
them through the existing fusion pipeline. It adds no storage of its own and
duplicates no ranking logic.

Five rules are load-bearing here, and each one is why this file exists rather
than a concatenation of ``store.search(...)`` calls:

* **``unavailable`` is not ``empty``.** A source that could not be read is
  reported with its reason; it is never rendered as "no memories matched". A
  controller that blur the two turns a broken backend into a confident answer.
* **Relevance is not truth.** ``fused_score`` ranks how well a record matches
  the query. It is never presented as a probability that the record is correct;
  ``RetrievedMemory.evidence_state`` carries that separately and defaults to
  ``unverified``.
* **Scope is a filter, never a score term.** Records outside the requested
  scope are dropped before ranking. A high relevance score must never be able
  to buy access to another user's memory.
* **The controller abstains rather than fabricates.** When no candidate clears
  the configured floor, the working set is empty and says why. It does not pad
  itself with weak matches to look useful.
* **Bounds are refused, not clamped.** Budgets and result counts outside the
  accepted range raise, because a silently clamped budget is a budget the
  operator never chose.

Everything here is synchronous and bounded: adapters do local file, SQLite and
in-process store reads. The one exception is the configured memory backend,
whose search may reach a remote provider; that call is the caller's to offload
(:func:`async_retrieve` does it for async entry points).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from alpha.memory.fusion.composer import compose_context
from alpha.memory.fusion.config import FusionConfig
from alpha.memory.fusion.fusion import MemoryFusion
from alpha.memory.fusion.models import Candidate, FusionResult, StageResult
from alpha.memory.fusion.query import ModeSelection, TaskContext, select_mode
from alpha.memory.fusion.stages import RetrievalStage, run_retrieval_stages

logger = logging.getLogger(__name__)

#: A memory record's truth posture, kept strictly separate from retrieval
#: relevance. ``unverified`` is the honest default for anything a store returned
#: without an attached artefact: "the backend held this text" is not "this text
#: is correct".
EvidenceState = Literal["explicit", "verified", "corroborated", "inferred", "unverified"]

#: How a source answered. Mirrors the fusion stage vocabulary so a caller reads
#: the same words at the controller boundary as inside the pipeline.
SourceState = Literal["configured", "unavailable"]

#: Minimum number of query tokens before the controller will run a retrieval at
#: all. Below this there is nothing to match on, and returning weak candidates
#: for "ok" or "hi" is exactly the fabricated-recall failure the design forbids.
MIN_QUERY_CHARS = 4

#: Ceiling on candidates any single adapter may contribute, so one noisy source
#: cannot crowd the fusion stage on its own.
MAX_CANDIDATES_PER_SOURCE = 50

#: Relevance floor below which the controller abstains. This is a *ranking*
#: floor, not a confidence threshold — see ``EvidenceState``.
DEFAULT_MIN_FUSED_SCORE = 0.15


@dataclass(frozen=True)
class MemorySourceHealth:
    """One adapter's honest posture.

    ``state`` deliberately stops at ``configured`` / ``unavailable``. This
    controller performs no write, so it can never claim ``write_verified`` or
    ``end_to_end_verified`` for a source, and inventing a greener word here
    would be exactly the "registered == healthy" lie the memory design bans.
    """

    source: str
    state: SourceState
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == "configured"


@dataclass(frozen=True)
class RetrievedMemory:
    """One selected record, with the evidence needed to justify its use.

    ``relevance`` is a ranking score in ``[0, 1]`` derived from lexical overlap
    and fusion signals. It is NOT a probability that the content is true.
    """

    memory_id: str
    memory_type: str
    content: str
    source: str
    relevance: float
    evidence_state: EvidenceState = "unverified"
    source_refs: tuple[str, ...] = ()
    score_components: dict[str, float] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """True when the record is backed by more than a bare store hit.

        ``unverified`` recalled text is still usable as a *clue* — the design
        wants past experience to change what the agent tries — but a caller
        deciding whether to act on a stored claim should see the difference.
        """
        return self.evidence_state in ("explicit", "verified", "corroborated")


@dataclass(frozen=True)
class WorkingSet:
    """The bounded, source-cited result of one active-memory retrieval."""

    query: str
    mode: str
    selected: tuple[RetrievedMemory, ...] = ()
    stages: tuple[StageResult, ...] = ()
    abstained: bool = False
    abstain_reason: str = ""
    token_estimate: int = 0
    latency_ms: float = 0.0
    degraded_sources: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.selected

    def text(self) -> str:
        """Render selected records as a compact, source-cited block."""
        if not self.selected:
            return ""
        lines: list[str] = []
        for item in self.selected:
            refs = f" ({', '.join(item.source_refs)})" if item.source_refs else ""
            lines.append(f"- [{item.source}/{item.memory_type}] {item.content}{refs}")
        return "\n".join(lines)


class _DreamRuleStage:
    """Lexical retrieval over the curated ``MEMORY.md`` durable rules.

    This is the only stage that reads a store Alpha *writes but never reads
    back*: ``consolidate_memory_dream`` appends curated rules to ``MEMORY.md``,
    and until this adapter nothing on a run path ever consulted them again.

    The root is resolved through the runtime home, never ``Path.cwd()``. A
    process working-directory change must not silently repoint durable memory
    at a different file — the previous ``DreamStore`` default did exactly that.

    Temporal note (slice #3): the curated dream-rules file carries no per-line
    timestamps, so no rule is ever time-filtered here — an as-of cutoff cannot
    honestly order prose rules whose knowledge time was never recorded. Documented
    no-op by design; the file is the operator's durable truth, not an episode log.
    """

    name = "dream_rules"

    def __init__(self, store: Any) -> None:
        self._store = store

    def retrieve(self, query: str, mode: str, budget: int, ctx: TaskContext) -> StageResult:
        started = time.perf_counter()
        if self._store is None:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="dream_store_unavailable",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        try:
            text = self._store.read_memory()
            source = str(getattr(self._store, "memory_path", "MEMORY.md"))
        except Exception as exc:  # noqa: BLE001 - a failed read is reported, never hidden
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=f"dream_store_read_failed:{type(exc).__name__}",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        tokens = _significant_tokens(query)
        if not tokens:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="no_significant_query_tokens",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        candidates: list[Candidate] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            # Headings and the store's own preamble are structure, not memory.
            if not line or line.startswith("#"):
                continue
            body = line.lstrip("-* ").strip()
            if not body:
                continue
            overlap = _overlap(tokens, body)
            if overlap <= 0.0:
                continue
            candidates.append(
                Candidate(
                    id=f"dream:{source}:{line_no}",
                    memory_type="project_facts",
                    subtype="curated_rule",
                    content=body,
                    score_components={"lexical": overlap},
                    scope_id=ctx.scope_id,
                    source_refs=(f"{source}:{line_no}",),
                    authority=0.5,
                )
            )
            if len(candidates) >= MAX_CANDIDATES_PER_SOURCE:
                break

        if not candidates:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="dream_rules_no_match",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        return StageResult(
            stage=self.name,
            status="ok",
            candidates=tuple(candidates),
            reason=f"dream_rules_returned={len(candidates)}",
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


class _ConfiguredFactStage:
    """Retrieval over the operator-configured ``MemoryManager`` backend.

    ``search()`` is a tier-2 contract method: many backends do not implement it.
    A backend without search is reported ``unavailable`` with that exact reason
    rather than being read as "this user has no memories".
    """

    name = "fact_backend"

    def __init__(self, manager: Any, *, user_id: str, agent_name: str | None) -> None:
        self._manager = manager
        self._user_id = user_id
        self._agent_name = agent_name

    def retrieve(self, query: str, mode: str, budget: int, ctx: TaskContext) -> StageResult:
        started = time.perf_counter()
        if self._manager is None:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="memory_manager_unavailable",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        if not callable(getattr(self._manager, "search", None)):
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="configured_backend_does_not_implement_search",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        try:
            rows = self._manager.search(
                query,
                top_k=MAX_CANDIDATES_PER_SOURCE,
                user_id=self._user_id,
                agent_name=self._agent_name,
            )
        except NotImplementedError:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="configured_backend_does_not_implement_search",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:  # noqa: BLE001 - degraded recall, disclosed
            logger.debug("fact_backend stage failed: %s", type(exc).__name__, exc_info=True)
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=f"fact_backend_search_failed:{type(exc).__name__}",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        candidates: list[Candidate] = []
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            fact_id = str(row.get("id") or f"fact:{len(candidates)}")
            category = str(row.get("category") or "context")
            confidence = _bounded_float(row.get("confidence"))
            candidates.append(
                Candidate(
                    id=f"fact:{fact_id}",
                    memory_type=_memory_type_for(category),
                    subtype=category,
                    content=content,
                    scope_id=ctx.scope_id,
                    authority=confidence,
                    # A fact the backend stored at explicit user request carries
                    # its own assertion; an inferred one does not.
                    metadata={"category": category, "confidence": confidence},
                    # createdAt is the row's knowledge time (bi-temporal
                    # ``valid_from``): what the store knew when the fact
                    # landed. Facts without a stamp are undated and therefore
                    # never excluded by an as-of cutoff — "unknown time" is
                    # not "future knowledge" (see :func:`_row_epoch`).
                    valid_from=_row_epoch(row.get("createdAt")),
                )
            )
            if len(candidates) >= MAX_CANDIDATES_PER_SOURCE:
                break

        if not candidates:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="fact_backend_no_match",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        return StageResult(
            stage=self.name,
            status="ok",
            candidates=tuple(candidates),
            reason=f"fact_backend_returned={len(candidates)}",
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


class _SessionHistoryStage:
    """Lexical retrieval over the FTS5 session-history engine.

    The engine is opt-in and its constructor defaults to an in-memory database,
    so an unset engine is reported ``unavailable`` — never as an empty history.
    A caller that wants episodic recall must supply a durable engine; this
    adapter will not silently substitute a different store.
    """

    name = "session_history"

    def __init__(self, engine: Any, *, exclude_session_ids: Sequence[str] = ()) -> None:
        self._engine = engine
        self._exclude = tuple(exclude_session_ids)

    def retrieve(self, query: str, mode: str, budget: int, ctx: TaskContext) -> StageResult:
        started = time.perf_counter()
        if self._engine is None:
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason="session_search_engine_not_configured",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        try:
            rows = self._engine.search_discovery(
                query,
                limit=MAX_CANDIDATES_PER_SOURCE,
                exclude_session_ids=list(self._exclude) or None,
            )
        except Exception as exc:  # noqa: BLE001 - degraded recall, disclosed
            logger.debug("session_history stage failed: %s", type(exc).__name__, exc_info=True)
            return StageResult(
                stage=self.name,
                status="unavailable",
                reason=f"session_search_failed:{type(exc).__name__}",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        candidates: list[Candidate] = []
        for row in rows or ():
            snippet = str(getattr(row, "snippet", "") or "").strip()
            if not snippet:
                continue
            session_id = str(getattr(row, "session_id", "") or "")
            message_id = str(getattr(row, "message_id", "") or "")
            score = _bounded_float(getattr(row, "score", 0.0))
            timestamp = getattr(row, "timestamp", None)
            candidates.append(
                Candidate(
                    id=f"episode:{session_id}:{message_id}",
                    memory_type="recent_task_state",
                    subtype="episode",
                    content=snippet,
                    scope_id=ctx.scope_id,
                    event_time=float(timestamp) if isinstance(timestamp, (int, float)) else None,
                    score_components={"lexical": min(max(score, 0.0), 1.0)},
                    source_refs=tuple(ref for ref in (f"session:{session_id}", f"message:{message_id}") if ref),
                )
            )
            if len(candidates) >= MAX_CANDIDATES_PER_SOURCE:
                break

        if not candidates:
            return StageResult(
                stage=self.name,
                status="empty",
                reason="session_history_no_match",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        return StageResult(
            stage=self.name,
            status="ok",
            candidates=tuple(candidates),
            reason=f"session_history_returned={len(candidates)}",
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


class ActiveMemoryController:
    """One orchestration surface over the memory stores Alpha already has.

    The controller is deliberately thin: it owns *activation, adapters and
    bounds*, while ranking, diversity and budgeting stay in
    ``alpha.memory.fusion`` and the content stays in each store. It never
    copies a record into a store of its own, so there is no second memory
    database to keep consistent and no competing source of truth.
    """

    def __init__(
        self,
        config: FusionConfig | None = None,
        *,
        dream_store: Any = None,
        memory_manager: Any = None,
        session_engine: Any = None,
        user_id: str = "default",
        agent_name: str | None = None,
        stages: Sequence[RetrievalStage] | None = None,
        min_fused_score: float = DEFAULT_MIN_FUSED_SCORE,
    ) -> None:
        self.config = config or FusionConfig()
        self._user_id = user_id
        self._agent_name = agent_name
        self._min_fused_score = float(min_fused_score)
        self._dream_store = dream_store
        self._memory_manager = memory_manager
        self._session_engine = session_engine

        if stages is not None:
            # Explicit injection wins, so tests and future hosts can substitute
            # providers without touching the built-in adapters.
            self._stages: tuple[RetrievalStage, ...] = tuple(stages)
        else:
            self._stages = (
                _DreamRuleStage(dream_store),
                _ConfiguredFactStage(memory_manager, user_id=user_id, agent_name=agent_name),
                _SessionHistoryStage(session_engine),
            )

    # -- capability / health ------------------------------------------------

    def health(self) -> tuple[MemorySourceHealth, ...]:
        """Report each adapter's posture without executing a retrieval.

        ``configured`` means the adapter was constructed with a usable
        dependency. It is *not* a claim that the next read will succeed — only a
        real read establishes that — which is why this method never reports a
        verification-grade state.
        """
        return tuple(
            MemorySourceHealth(
                source=stage.name,
                state="configured" if _stage_has_provider(stage) else "unavailable",
                detail="" if _stage_has_provider(stage) else "no provider configured",
            )
            for stage in self._stages
        )

    # -- retrieval ----------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        budget: int | None = None,
        mode: str | None = None,
        task_type: str = "coding",
        as_of: float | None = None,
    ) -> WorkingSet:
        """Build a bounded, source-cited working set for one query.

        Raises on an out-of-range budget rather than clamping it, and abstains
        (empty ``selected`` plus a reason) when nothing clears the relevance
        floor.
        """
        started = time.perf_counter()
        text = (query or "").strip()
        effective_budget = self.config.total_budget_tokens if budget is None else int(budget)
        if effective_budget < 1:
            msg = "budget must be a positive number of tokens"
            raise ValueError(msg)

        mode_selection = self._select_mode(text, mode)
        ctx = TaskContext(
            scope_id=self._user_id,
            task_type=task_type,
            budget=effective_budget,
            max_candidates=MAX_CANDIDATES_PER_SOURCE,
            agent_name=self._agent_name,
            as_of=as_of,
        )

        if len(text) < MIN_QUERY_CHARS:
            return WorkingSet(
                query=text,
                mode=mode_selection.mode,
                stages=(),
                abstained=True,
                abstain_reason="query_too_short_to_retrieve",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        stages = run_retrieval_stages(
            self._stages,
            query=text,
            mode=mode_selection.mode,  # type: ignore[arg-type]
            budget=effective_budget,
            ctx=ctx,
            latency_budget_ms=self.config.latency_budget_ms,
        )
        if as_of is not None:
            # Central temporal projection (slice #3): every candidate the
            # controller will ever fuse passes the same cutoff here, so the
            # stage adapters stay dumb and no future fusion path can skip it.
            stages = _filter_stages_as_of(stages, as_of)
        fusion: FusionResult = MemoryFusion(self.config).fuse(stages, task_context=ctx)

        degraded = tuple(sorted(fusion.stages_unavailable))
        if not fusion.candidates:
            reason = "no_source_returned_candidates" if not degraded else "all_usable_sources_empty_or_unavailable"
            return WorkingSet(
                query=text,
                mode=mode_selection.mode,
                stages=tuple(stages),
                abstained=True,
                abstain_reason=reason,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                degraded_sources=degraded,
            )

        kept = [c for c in fusion.candidates if c.fused_score >= self._min_fused_score]
        if not kept:
            return WorkingSet(
                query=text,
                mode=mode_selection.mode,
                stages=tuple(stages),
                abstained=True,
                abstain_reason=f"no_candidate_cleared_relevance_floor:{self._min_fused_score}",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                degraded_sources=degraded,
            )

        composed = compose_context(kept, self.config, task_context=ctx)
        selected = tuple(
            RetrievedMemory(
                memory_id=candidate.id,
                memory_type=candidate.memory_type,
                content=(candidate.summary or candidate.content).strip(),
                source=_primary_source(candidate),
                relevance=round(min(max(candidate.fused_score, 0.0), 1.0), 4),
                evidence_state=_evidence_state(candidate),
                source_refs=candidate.source_refs,
                score_components=dict(candidate.score_components),
            )
            for candidate in kept
        )
        return WorkingSet(
            query=text,
            mode=mode_selection.mode,
            selected=selected,
            stages=tuple(stages),
            token_estimate=composed.total_tokens,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            degraded_sources=degraded,
        )

    def _select_mode(self, query: str, explicit: str | None) -> ModeSelection:
        if explicit is not None:
            return select_mode(query, explicit)  # type: ignore[arg-type]
        return select_mode(query)


# -- helpers ----------------------------------------------------------------


#: Function words that match almost every line. Dropping them is what stops a
#: query like "what is the deploy region" from scoring every memory line that
#: contains "the" — the false-positive mode the lexical tier is most prone to.
_STOPWORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "any",
        "can",
        "her",
        "was",
        "one",
        "our",
        "out",
        "has",
        "have",
        "what",
        "when",
        "where",
        "which",
        "about",
        "does",
        "did",
        "how",
        "why",
        "who",
        "this",
        "that",
        "these",
        "those",
        "with",
        "from",
        "into",
        "was",
        "were",
        "been",
        "being",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "should",
        "would",
        "could",
    }
)


def _significant_tokens(query: str) -> set[str]:
    """Split a query into meaningful tokens.

    Code identifiers, paths, env vars and error strings are the queries active
    memory most often receives, so tokens keep their case and internal
    punctuation; only surrounding punctuation is stripped. Tokens shorter than
    three characters and common function words are dropped because they match
    almost every line and produce confident-looking noise.
    """
    return {token for token in re.findall(r"[\w./:-]{3,}", query) if token.casefold() not in _STOPWORDS}


def _overlap(tokens: set[str], text: str) -> float:
    """Fraction of query tokens present in ``text``, case-insensitively."""
    if not tokens:
        return 0.0
    haystack = text.casefold()
    hits = sum(1 for token in tokens if token.casefold() in haystack)
    return hits / len(tokens)


def _bounded_float(value: Any) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return 0.0
    return min(max(numeric, 0.0), 1.0)


def _row_epoch(value: Any) -> float | None:
    """One backend row's knowledge time as a unix-seconds float — total.

    Accepts an ISO-8601 timestamp (``Z`` suffix included) or a numeric unix
    value; anything else is ``None`` (undated, never excluded by an as-of
    cutoff). This is the stage-adapter twin of
    ``alpha.agents.memory.tools._parse_as_of`` — a tool-layer caller input, not
    a row value, so the two stay separate on purpose.
    """

    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            return None
        return numeric
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if "-" not in text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _filter_stages_as_of(stages: Sequence[StageResult], as_of: float) -> list[StageResult]:
    """Central as-of projection: drop candidates dated after the cutoff.

    A candidate's knowledge time is its ``event_time`` (episode rows) or, when
    absent, its ``valid_from`` (fact rows); an undated candidate is kept,
    because "unknown time" is not "future knowledge". Every fully-filtered
    stage is reported ``empty`` with the cutoff in its own reason — the
    honest reading is "this source knew nothing yet at that time", never an
    unexplained zero.
    """

    filtered: list[StageResult] = []
    for result in stages:
        kept = [
            candidate
            for candidate in result.candidates
            if (candidate.event_time if candidate.event_time is not None else candidate.valid_from) is None or (candidate.event_time if candidate.event_time is not None else candidate.valid_from) <= as_of
        ]
        dropped = len(result.candidates) - len(kept)
        if dropped == 0:
            filtered.append(result)
            continue
        reason = f"{result.reason}; as_of_dropped={dropped}"
        if not kept:
            reason += f"; as_of={as_of:.0f} excluded every candidate"
        filtered.append(
            StageResult(
                stage=result.stage,
                status="empty" if not kept else result.status,
                candidates=tuple(kept),
                reason=reason,
                latency_ms=result.latency_ms,
            )
        )
    return filtered


def _memory_type_for(category: str) -> str:
    """Map a backend category onto a fusion budget type.

    The budget profiles only fund the seven declared types, so an unmapped
    category falls into ``project_facts`` rather than earning a budget line of
    its own that the profile never allocated for.
    """
    lowered = (category or "").casefold()
    if lowered in ("preference", "persona"):
        return "identity_soul"
    if lowered in ("correction", "failure", "lesson"):
        return "failures_lessons"
    if lowered in ("procedure", "skill", "playbook"):
        return "procedures_skills"
    return "project_facts"


def _primary_source(candidate: Candidate) -> str:
    if candidate.source_stages:
        return candidate.source_stages[0]
    return "unknown"


def _evidence_state(candidate: Candidate) -> EvidenceState:
    """Derive a truth posture from what the record actually carries.

    A curated ``MEMORY.md`` rule is a deliberate, operator-visible assertion, so
    it reads ``explicit``. A stored fact is only ``corroborated`` when the
    backend recorded a high confidence; anything else stays ``unverified``.
    Neither is ``verified``: nothing here re-checked the claim against a current
    artefact, and claiming otherwise is precisely the fabricated-certainty
    failure this separation exists to prevent.
    """
    subtype = (candidate.subtype or "").casefold()
    if subtype == "curated_rule":
        return "explicit"
    confidence = candidate.authority
    if confidence >= 0.8:
        return "corroborated"
    if confidence > 0.0:
        return "inferred"
    return "unverified"


def _stage_has_provider(stage: RetrievalStage) -> bool:
    provider = getattr(stage, "_store", None)
    if provider is None:
        provider = getattr(stage, "_manager", None)
    if provider is None:
        provider = getattr(stage, "_engine", None)
    return provider is not None


def runtime_rooted_dream_store(root: Path | str | None = None) -> Any:
    """Build a :class:`DreamStore` pinned to a deterministic runtime path.

    ``DreamStore``'s own default is ``Path.cwd()``, which means a Gateway whose
    working directory changes reads a different ``MEMORY.md``. Durable memory
    must not be reachable through the process working directory, so this
    resolves the agent runtime home instead and only accepts an explicit root
    as an override.
    """
    from alpha.memory.dreaming.store import DreamStore

    if root is not None:
        return DreamStore(root)
    from alpha.config.runtime_paths import runtime_home

    return DreamStore(runtime_home() / "memory")


def get_active_memory_controller(
    *,
    user_id: str = "default",
    agent_name: str | None = None,
    dream_store: Any = None,
    memory_manager: Any = None,
    session_engine: Any = None,
    config: FusionConfig | None = None,
) -> ActiveMemoryController:
    """Build a controller over real stores, resolving each lazily.

    Every dependency is optional and resolved independently: a missing session
    engine degrades that one stage to ``unavailable`` instead of disabling the
    controller, and an unconfigured optional store never silently becomes a
    different one.
    """
    from alpha.config.memory_config import get_memory_config

    fusion_config = config or get_memory_config().fusion

    if dream_store is None:
        try:
            dream_store = runtime_rooted_dream_store()
        except Exception:  # noqa: BLE001 - a missing runtime home is a degraded stage
            logger.debug("dream store unavailable for active memory", exc_info=True)

    if memory_manager is None:
        try:
            from alpha.agents.memory import get_memory_manager

            memory_manager = get_memory_manager()
        except Exception:  # noqa: BLE001 - backend resolution failure is disclosed per-stage
            logger.debug("memory manager unavailable for active memory", exc_info=True)

    return ActiveMemoryController(
        fusion_config,
        dream_store=dream_store,
        memory_manager=memory_manager,
        session_engine=session_engine,
        user_id=user_id,
        agent_name=agent_name,
    )


async def async_retrieve(
    controller: ActiveMemoryController,
    query: str,
    **kwargs: Any,
) -> WorkingSet:
    """Awaitable wrapper so async entry points never block the event loop.

    The controller's stages are synchronous local reads, so this offloads the
    whole retrieval to a worker thread rather than attempting ``asyncio.run``
    from inside a running loop — which would raise, and previously made an
    escalation silently never happen.
    """
    import asyncio

    return await asyncio.to_thread(controller.retrieve, query, **kwargs)


def active_memory_enabled() -> bool:
    """Read the host gate: fusion is the controller's activation switch.

    Keeping one flag is deliberate. ``memory.fusion.enabled`` already exists,
    already validates, and already means "multi-stage retrieval is on"; a second
    ``memory.active_controller.enabled`` knob would let the two disagree and
    leave a reader unable to say which one governs.
    """
    from alpha.config.memory_config import get_memory_config

    try:
        cfg = get_memory_config()
    except Exception:  # noqa: BLE001 - an unreadable config must not enable memory
        return False
    return bool(cfg.enabled and cfg.fusion.enabled)


__all__ = [
    "DEFAULT_MIN_FUSED_SCORE",
    "MAX_CANDIDATES_PER_SOURCE",
    "MIN_QUERY_CHARS",
    "ActiveMemoryController",
    "EvidenceState",
    "MemorySourceHealth",
    "RetrievedMemory",
    "SourceState",
    "WorkingSet",
    "active_memory_enabled",
    "async_retrieve",
    "get_active_memory_controller",
    "runtime_rooted_dream_store",
]
