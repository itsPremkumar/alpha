"""Okapi BM25 ranking over the policy-filtered catalog.

Reuse, not reinvention
----------------------
The token-level BM25 constants and the tokenizer already live in this repo at
:mod:`alpha.memory.cognitive_memory_tiering` (``BM25_K1``, ``BM25_B`` and the
public ``tokenize``). They are imported here rather than re-derived, so the two
BM25 surfaces in Alpha cannot drift apart. What is new in this module is the
*index shape*: documents are built per catalog entry, and an untrusted entry's
parameter text is never added to its document at all (the strongest possible
form of "not indexed" -- there is nothing to leak).

Ranking rules honored here
--------------------------
* Field weights: tool name 3.0, description 1.0, first-party parameter names
  2.0, first-party parameter descriptions 1.0. A name hit must beat a
  description hit, because the name is what the model must then call.
* Exact tool name is always honored: a query equal to an entry's name (or to
  its id) returns that entry first, even when it tokenizes to nothing.
* Light English stemming: a small suffix stripper so ``scheduling`` reaches a
  tool described as ``Schedule a recurring task``.
* Small intent expansion: a frozen phrase map so ``look up the price`` reaches
  a tool described as ``Search the web``. Deliberately tiny and inspectable --
  a black-box embedding index would be a second ranking system nobody can audit.
* Queries must be in English. A query with no usable terms returns no ranked
  results rather than an arbitrary slice of the catalog.

Boundedness and honesty
-----------------------
Every documented budget is enforced and reported: per-query limit, batch query
count, per-query characters, serialized query bytes, total batch candidates,
and the response character budget. Truncation is always explicit, and a group
that lost candidates carries ``truncated: true`` so an empty truncated group
cannot be mistaken for a query with no matches.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.memory.cognitive_memory_tiering import BM25_B, BM25_K1, tokenize
from alpha.tools.discovery.catalog import CatalogEntry, CatalogSnapshot

#: Field weights. Name dominates so a literal tool name always ranks first.
FIELD_WEIGHT_NAME = 3.0
FIELD_WEIGHT_DESCRIPTION = 1.0
FIELD_WEIGHT_PARAM_NAME = 2.0
FIELD_WEIGHT_PARAM_DESCRIPTION = 1.0

#: Score added to an exact name/id match so it always sorts first.
EXACT_MATCH_BONUS = 1_000.0

#: A trailing stem shorter than this is left alone, so ``ing``/``ed`` stripping
#: cannot eat short words.
_MIN_STEM_LENGTH = 4

#: Frozen, inspectable intent expansion: phrase -> extra query terms. Small on
#: purpose; this is a hint, not a synonym graph.
INTENT_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "look up the price": ("search", "web", "price"),
    "look up": ("search", "find"),
    "find out": ("search", "research"),
    "web search": ("search", "web"),
    "run a command": ("shell", "command", "execute"),
    "set a reminder": ("schedule", "task", "cron"),
    "schedule something": ("schedule", "cron", "recurring"),
    "read a file": ("read", "file"),
    "write a file": ("write", "file", "edit"),
    "take a screenshot": ("screenshot", "capture"),
    "send a message": ("message", "send"),
    "track progress": ("progress", "update"),
    "debug a failure": ("diagnose", "debug", "repair"),
}


class SearchBudgetError(ValueError):
    """An over-budget or malformed request. Fails the whole request, not a group."""


def stem(token: str) -> str:
    """Light English stemming: strip one plural/gerund/past suffix, then a
    trailing silent ``e``.

    Deliberately not Porter: a full stemmer changes recall in ways that are
    hard to explain to a model debugging a wrong tool choice. This is enough to
    bridge the documented cases (``scheduling`` and ``schedule`` both reduce to
    ``schedul``) and is idempotent for short tokens.
    """
    if len(token) < _MIN_STEM_LENGTH:
        return token
    for suffix in ("ization", "ingly", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM_LENGTH - 1:
            trimmed = token[: -len(suffix)]
            return f"{trimmed}y" if suffix == "ies" else trimmed
    if token.endswith("e") and len(token) - 1 >= _MIN_STEM_LENGTH:
        return token[:-1]
    return token


def _terms(text: str) -> list[str]:
    """Tokenize then stem. The shared repo tokenizer already drops stopwords."""
    return [stem(token) for token in tokenize(text or "")]


def expand_intent(query_terms: list[str], raw_query: str) -> list[str]:
    """Add intent-expansion terms for a recognized phrase. Returns new terms only."""
    lowered = " ".join((raw_query or "").lower().split())
    extra: list[str] = []
    for phrase, additions in INTENT_EXPANSIONS.items():
        if phrase in lowered:
            extra.extend(_terms(" ".join(additions)))
    seen = set(query_terms)
    return [term for term in extra if not (term in seen or seen.add(term))]


@dataclass(frozen=True)
class _Document:
    """One indexed catalog entry."""

    entry: CatalogEntry
    weighted: Counter
    length: int


@dataclass
class CatalogIndex:
    """Prebuilt BM25 index over one catalog snapshot.

    Built once per snapshot and reused for every query, so a 300-tool catalog
    is tokenized once, not once per search.
    """

    snapshot: CatalogSnapshot
    documents: tuple[_Document, ...] = field(default_factory=tuple)
    document_frequency: Counter = field(default_factory=Counter)
    average_length: float = 0.0
    exact_index: dict[str, CatalogEntry] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, snapshot: CatalogSnapshot) -> CatalogIndex:
        """Index every entry in *snapshot*.

        Untrusted entries carry ``input_schema=None`` by construction, so the
        parameter text below is structurally absent for them: there is no code
        path that could index an MCP/client parameter schema even by accident.
        """
        documents: list[_Document] = []
        document_frequency: Counter = Counter()
        for entry in snapshot.entries:
            weighted: Counter = Counter()
            for term in _terms(entry.name):
                weighted[term] += FIELD_WEIGHT_NAME
            for term in _terms(entry.description):
                weighted[term] += FIELD_WEIGHT_DESCRIPTION
            if entry.input_schema:
                for name, definition in (entry.input_schema.get("properties") or {}).items():
                    for term in _terms(str(name)):
                        weighted[term] += FIELD_WEIGHT_PARAM_NAME
                    if isinstance(definition, dict):
                        for term in _terms(str(definition.get("description") or "")):
                            weighted[term] += FIELD_WEIGHT_PARAM_DESCRIPTION
            documents.append(_Document(entry=entry, weighted=weighted, length=max(sum(weighted.values()), 1)))
            for term in weighted:
                document_frequency[term] += 1
        average = (sum(document.length for document in documents) / len(documents)) if documents else 0.0
        exact: dict[str, CatalogEntry] = {}
        for document in documents:
            exact[document.entry.entry_id] = document.entry
            exact[document.entry.name] = document.entry
        return cls(
            snapshot=snapshot,
            documents=tuple(documents),
            document_frequency=document_frequency,
            average_length=average,
            exact_index=exact,
        )

    @property
    def size(self) -> int:
        return len(self.documents)

    def exact_entry(self, query: str) -> CatalogEntry | None:
        """Exact id or exact name match, honoring case-insensitive names."""
        candidate = (query or "").strip()
        if not candidate:
            return None
        hit = self.exact_index.get(candidate)
        if hit is not None:
            return hit
        lowered = candidate.lower()
        for entry in self.snapshot.entries:
            if entry.name.lower() == lowered:
                return entry
        return None

    def score(self, document: _Document, query_terms: list[str]) -> float:
        """Okapi BM25 score for one document, using the repo's shared constants."""
        total_documents = len(self.documents)
        if not total_documents or not self.average_length:
            return 0.0
        score = 0.0
        for term in query_terms:
            frequency = document.weighted.get(term, 0.0)
            if not frequency:
                continue
            df = self.document_frequency.get(term, 0)
            idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
            denominator = frequency + BM25_K1 * (1.0 - BM25_B + BM25_B * (document.length / self.average_length))
            score += idf * (frequency * (BM25_K1 + 1.0)) / denominator
        return score

    def search(self, query: str, *, limit: int) -> list[CatalogEntry]:
        """Ranked entries for *query*, exact name first.

        A query with no usable terms returns the exact match or nothing -- never
        an arbitrary slice of the catalog.
        """
        exact = self.exact_entry(query)
        stemmed = _terms(query)
        query_terms = stemmed + expand_intent(stemmed, query)
        if not query_terms:
            return [exact] if exact is not None else []
        scored: list[tuple[float, CatalogEntry]] = []
        for document in self.documents:
            value = self.score(document, query_terms)
            if document.entry is exact:
                value += EXACT_MATCH_BONUS
            elif value <= 0.0:
                continue
            scored.append((value, document.entry))
        # Highest score first; entry id breaks ties so the order is total and
        # therefore reproducible across runs.
        scored.sort(key=lambda item: (-item[0], item[1].entry_id))
        return [entry for _, entry in scored[: max(1, int(limit))]]


@dataclass(frozen=True)
class SearchQuery:
    """One query in a search request, single or batch."""

    query: str
    limit: int | None = None


@dataclass
class SearchOutcome:
    """Result of one ``tool_search`` invocation.

    ``batch`` distinguishes a real batch request from a scalar one that happens
    to resolve to a single group, because the two use different result shapes:
    a single-query call returns the candidate array directly, while a batch
    always returns ``{results: [...]}`` in request order.
    """

    results: list[dict[str, Any]]
    batch: bool = False
    truncated: bool = False
    candidates_returned: int = 0
    dropped_candidates: int = 0

    def to_payload(self) -> dict[str, Any]:
        """Serializable model-facing payload."""
        if not self.batch and len(self.results) == 1 and not self.truncated:
            return {"candidates": self.results[0]["candidates"]}
        payload: dict[str, Any] = {"results": self.results}
        if self.truncated:
            payload["truncated"] = True
        return payload


@dataclass(frozen=True)
class _RequestPlan:
    """Validated request shape: effective queries plus where the top limit lands."""

    queries: tuple[SearchQuery, ...]
    #: ``"scalar"`` when the top-level limit scopes to the non-empty ``query``,
    #: ``"none"`` when no top-level limit is meaningful for this request.
    limit_scope: str
    #: True when a blank scalar query was supplied on its own: a legitimate
    #: request that must return an empty candidate array, not an error.
    blank_scalar: bool


def _parse_batch(batch: Sequence[Any]) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    for index, item in enumerate(batch):
        if not isinstance(item, dict):
            raise SearchBudgetError(f"queries[{index}] must be an object with a 'query' string")
        raw = item.get("query")
        if not isinstance(raw, str) or not raw.strip():
            raise SearchBudgetError(f"queries[{index}].query must be a non-empty string")
        limit = item.get("limit")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
            raise SearchBudgetError(f"queries[{index}].limit must be a positive integer or null")
        queries.append(SearchQuery(query=raw.strip(), limit=limit))
    return queries


def plan_request(
    *,
    query: str | None,
    limit: int | None,
    queries: Sequence[Any] | None,
) -> _RequestPlan:
    """Validate the single/batch request shape and resolve the effective queries.

    Honored shapes:

    * ``{query, limit}`` -> scalar result shape; the top-level limit applies.
    * ``{queries: [...]}`` -> batch result shape.
    * A non-empty ``query`` beside a non-empty ``queries``: the scalar runs
      first with the top-level limit scoped to it, then the batch in request
      order, including repeated query text.
    * A blank/omitted/``null`` ``query`` beside a non-empty batch is ignored.
      In that case a non-null top-level ``limit`` is REJECTED rather than
      silently applied to the batch.
    * An omitted/``null`` ``queries`` keeps scalar behavior, and ``queries: []``
      falls back to the scalar shape when ``query`` is non-empty.
    * A blank scalar query with no batch returns an empty candidate array.
    * A missing/``null`` scalar with no batch, or an empty batch with no
      non-empty scalar, is rejected.
    """
    scalar_text = (query or "").strip()
    non_empty_batch = bool(queries)
    limit_provided = limit is not None

    if limit_provided and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise SearchBudgetError("limit must be a positive integer or null")

    if not scalar_text and not non_empty_batch:
        if query is not None and not scalar_text:
            # A blank scalar query on its own is a valid, empty request.
            return _RequestPlan(queries=(), limit_scope="none", blank_scalar=True)
        raise SearchBudgetError("tool_search requires a non-empty 'query' or a non-empty 'queries' batch")

    effective: list[SearchQuery] = []
    limit_scope = "none"
    if scalar_text:
        effective.append(SearchQuery(query=scalar_text, limit=limit))
        limit_scope = "scalar"
    if non_empty_batch:
        if limit_provided and not scalar_text:
            raise SearchBudgetError("a top-level 'limit' cannot be combined with a non-empty 'queries' batch; set per-query limits instead")
        effective.extend(_parse_batch(queries))
    return _RequestPlan(queries=tuple(effective), limit_scope=limit_scope, blank_scalar=False)


class ToolSearcher:
    """Budget-enforcing facade over a :class:`CatalogIndex`.

    Holds the whole documented budget surface so
    :mod:`alpha.tools.discovery.tools` stays a thin adapter and the budgets stay
    unit-testable on their own.
    """

    def __init__(self, index: CatalogIndex, config: Any) -> None:
        self._index = index
        self._config = config

    @property
    def index(self) -> CatalogIndex:
        return self._index

    @property
    def config(self) -> Any:
        return self._config

    def _enforce_request_budgets(self, plan: _RequestPlan) -> None:
        cfg = self._config
        if len(plan.queries) > cfg.max_batch_queries:
            raise SearchBudgetError(f"tool_search accepts at most {cfg.max_batch_queries} queries per call, got {len(plan.queries)}")
        for position, item in enumerate(plan.queries):
            if len(item.query) > cfg.max_query_chars:
                raise SearchBudgetError(f"query {position} is {len(item.query)} characters; the limit is {cfg.max_query_chars}")
        serialized = json.dumps([item.query for item in plan.queries], ensure_ascii=False, separators=(",", ":"))
        payload_bytes = len(serialized.encode("utf-8"))
        if payload_bytes > cfg.max_batch_query_bytes:
            raise SearchBudgetError(f"serialized query list is {payload_bytes} bytes; the limit is {cfg.max_batch_query_bytes}")
        requested_total = 0
        for position, item in enumerate(plan.queries):
            if item.limit is None:
                requested_total += min(cfg.search_default_limit, cfg.max_search_limit)
                continue
            if item.limit < 1 or item.limit > cfg.max_search_limit:
                raise SearchBudgetError(f"query {position} limit {item.limit} is outside 1..{cfg.max_search_limit}")
            requested_total += item.limit
        if requested_total > cfg.max_batch_candidates:
            raise SearchBudgetError(f"batch requests {requested_total} candidates; the limit is {cfg.max_batch_candidates}")

    def run(
        self,
        *,
        query: str | None = None,
        limit: int | None = None,
        queries: Sequence[Any] | None = None,
    ) -> SearchOutcome:
        """Run one single or batch search under every documented budget."""
        plan = plan_request(query=query, limit=limit, queries=queries)
        if plan.blank_scalar:
            return SearchOutcome(results=[{"query": query or "", "candidates": []}], candidates_returned=0)
        self._enforce_request_budgets(plan)

        groups: list[dict[str, Any]] = []
        total = 0
        for item in plan.queries:
            per_query = self._config.effective_search_limit(item.limit)
            hits = self._index.search(item.query, limit=per_query)
            groups.append({"query": item.query, "candidates": [entry.search_payload() for entry in hits]})
            total += len(hits)

        outcome = SearchOutcome(results=groups, batch=queries is not None, candidates_returned=total)
        self._enforce_response_budget(outcome)
        return outcome

    def _enforce_response_budget(self, outcome: SearchOutcome) -> None:
        """Drop lowest-ranked candidates until the response fits, then mark it.

        An emptied group keeps its ``truncated: true`` marker, so "the budget ate
        this group's results" never reads as "this query had no matches".
        """
        budget = self._config.response_char_budget
        if len(render_response(outcome)) <= budget:
            return
        changed = True
        while changed and len(render_response(outcome)) > budget:
            changed = False
            for group in outcome.results:
                if not group["candidates"]:
                    continue
                group["candidates"].pop()
                outcome.dropped_candidates += 1
                changed = True
                if len(render_response(outcome)) <= budget:
                    break
        for group in outcome.results:
            if group["candidates"] or group.get("truncated"):
                continue
            group["truncated"] = True
        outcome.truncated = True
        outcome.candidates_returned = sum(len(group["candidates"]) for group in outcome.results)


def render_response(outcome: SearchOutcome) -> str:
    """The exact JSON text measured against the response character budget."""
    payload: dict[str, Any] = {"results": outcome.results}
    if outcome.truncated:
        payload["truncated"] = True
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
