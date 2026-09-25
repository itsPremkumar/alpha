"""Deterministic case runner for injected memory providers.

The runner has no model, network, timer thread, global singleton, or import of
Alpha's production memory stack at module import time.  A provider implements
three synchronous methods:

``write(event)``
    Persist one :class:`~alpha.memory.evaluation.models.Event` and return an
    ID/receipt (a receipt may explicitly say ``stored=False``).
``recall(query, scope, now)``
    Return zero or more record-like values for the requested scope and fixed
    evaluation time.
``forget(record_id)``
    Remove a record by ID.

``ScriptedProvider`` is a pre-baked oracle used to test the harness itself;
its results are labelled ``scripted_oracle`` and are never presented as a real
memory-stack measurement.  Its default ``latency_ms`` is a deterministic zero
because no backend operation is timed; injected real-provider latency remains
observational.  ``RealStoreProvider`` supplies a small adapter seam
for a store instance, including the L1 ``put_records/search/delete_records``
shape, without making the evaluation package depend on a singleton.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from alpha.memory.evaluation.metrics import (
    answer_matches,
    is_refusal,
    recall_at_k,
    scope_matches,
    token_count,
    update_correctness,
    write_precision,
    write_recall,
)
from alpha.memory.evaluation.models import (
    Case,
    CaseResult,
    Event,
    MemoryRecord,
    Question,
    QuestionOutcome,
)

ProviderKind = Literal["real", "scripted"]


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot produce a measurement."""


UnavailableError = ProviderUnavailable


class ProviderProtocolError(RuntimeError):
    """Raised when an injected object does not expose the provider contract."""


@runtime_checkable
class MemoryProvider(Protocol):
    """Synchronous injected memory provider contract."""

    def write(self, event: Event) -> Any:
        """Persist one event and return an ID or write receipt."""

    def recall(self, query: str, scope: Mapping[str, Any], now: str) -> Sequence[Any]:
        """Return records relevant to a query in one scope at fixed ``now``."""

    def forget(self, id: str) -> Any:
        """Forget one record by ID."""


@dataclass(frozen=True)
class WriteReceipt:
    """Explicit write outcome used by scripted/oracle providers."""

    id: str
    stored: bool = True


class UnavailableProvider:
    """Small provider fake for fail-closed tests and offline smoke runs."""

    available = False

    def write(self, event: Event) -> WriteReceipt:
        raise ProviderUnavailable("memory provider is unavailable")

    def recall(self, query: str, scope: Mapping[str, Any], now: str) -> Sequence[Any]:
        raise ProviderUnavailable("memory provider is unavailable")

    def forget(self, id: str) -> None:
        raise ProviderUnavailable("memory provider is unavailable")


class ScriptedProvider:
    """Deterministic pre-baked provider for harness self-tests.

    ``responses`` maps exact question text to record-like values.  When it is
    omitted, :meth:`from_case` builds responses from the case's evidence IDs and
    expected values.  The latter is intentionally an oracle: it proves the
    runner/metrics contract, not the quality of a production memory backend.
    """

    provider_kind = "scripted"

    def __init__(
        self,
        responses: Mapping[str, Sequence[Any]] | None = None,
        *,
        records: Sequence[Any] | None = None,
        write_expectations: Mapping[str, bool] | None = None,
        unavailable: bool = False,
        name: str = "scripted-oracle",
    ) -> None:
        self.available = not unavailable
        self.name = name
        self.responses = {str(query): list(values) for query, values in (responses or {}).items()}
        self.write_expectations = dict(write_expectations or {})
        self.records = list(records or ())
        self.writes: list[Event] = []
        self.forgotten_ids: set[str] = set()

    @classmethod
    def from_case(cls, case: Case) -> ScriptedProvider:
        """Build a deterministic oracle whose recall responses match a case."""

        event_by_id = {event.id: event for event in case.events()}
        expected_ids = set(case.expected_stored_ids)
        responses: dict[str, list[MemoryRecord]] = {}
        for question in case.questions:
            if question.expects_abstention:
                responses.setdefault(question.question, [])
                continue
            selected_ids = question.evidence_ids or _infer_evidence_ids(case, question)
            selected: list[MemoryRecord] = []
            for evidence_id in selected_ids:
                event = event_by_id.get(evidence_id)
                if event is None or event.id not in expected_ids:
                    continue
                selected.append(
                    MemoryRecord(
                        id=event.id,
                        content=event.text,
                        scope=_event_scope(case, event),
                        answer=question.expected,
                        useful=True,
                        cited=True,
                        metadata={
                            "event_id": event.id,
                            "fact_id": event.fact_id or event.id,
                            "case_id": case.id,
                            "source": "scripted_case",
                        },
                    )
                )
            responses.setdefault(question.question, selected)
        return cls(
            responses,
            write_expectations={event.id: event.id in expected_ids for event in case.events()},
            name=f"scripted:{case.id}",
        )

    def write(self, event: Event) -> WriteReceipt:
        self._require_available()
        self.writes.append(event)
        stored = self.write_expectations.get(event.id, True)
        if stored:
            self.records.append(
                MemoryRecord(
                    id=event.id,
                    content=event.text,
                    scope=_event_scope_from_event(event),
                    answer=event.answer,
                    useful=True,
                    cited=True,
                    metadata={"event_id": event.id, "fact_id": event.fact_id or event.id, "source": "scripted_write"},
                )
            )
        return WriteReceipt(event.id, stored=stored)

    def recall(self, query: str, scope: Mapping[str, Any], now: str) -> Sequence[MemoryRecord]:
        self._require_available()
        if query in self.responses:
            return tuple(self.responses[query])
        normalized = str(query).strip().casefold()
        for key, values in self.responses.items():
            if key.strip().casefold() == normalized:
                return tuple(values)
        query_tokens = {token for token in normalized.split() if token}
        candidates = [record for record in self.records if record.id not in self.forgotten_ids]
        if not query_tokens:
            return tuple(candidates)
        matching = [record for record in candidates if query_tokens & set(record.content.casefold().split())]
        return tuple(matching or candidates)

    def forget(self, id: str) -> None:
        self._require_available()
        self.forgotten_ids.add(str(id))
        self.records = [record for record in self.records if record.id != str(id)]
        for query, values in self.responses.items():
            self.responses[query] = [record for record in values if record.id != str(id)]

    def _require_available(self) -> None:
        if not self.available:
            raise ProviderUnavailable("scripted provider is unavailable")


class RealStoreProvider:
    """Adapter for a real store instance or explicit callables.

    The preferred path is a provider that already exposes ``write/recall/
    forget``.  For Alpha's L1 store, the fallback calls are explicit and
    deterministic:

    * ``put_records([record_factory(event, scope)], user_id=..., agent_name=...)``
    * ``search(query, top_k=..., user_id=..., agent_name=...)``
    * ``delete_records([record_id], user_id=..., agent_name=...)``

    The L1 record constructor is injected by the host/wiring layer; this
    package intentionally does not import the agent-facing memory backend.
    A host with DeerMem or another backend can inject ``write_fn``,
    ``recall_fn``, and ``forget_fn`` without changing the harness.
    """

    provider_kind = "real"

    def __init__(
        self,
        store: Any,
        *,
        scope: Mapping[str, Any] | None = None,
        write_fn: Callable[[Event, Mapping[str, Any]], Any] | None = None,
        record_factory: Callable[[Event, Mapping[str, Any]], Any] | None = None,
        recall_fn: Callable[[str, Mapping[str, Any], str], Sequence[Any]] | None = None,
        forget_fn: Callable[[str, Mapping[str, Any]], Any] | None = None,
        top_k: int = 10,
    ) -> None:
        self.store = store
        self.scope = dict(scope or {})
        self._active_scope = dict(self.scope)
        self.write_fn = write_fn
        self.record_factory = record_factory
        self.recall_fn = recall_fn
        self.forget_fn = forget_fn
        self.top_k = max(1, int(top_k))

    def bind_scope(self, scope: Mapping[str, Any]) -> None:
        """Bind the current case scope for the scope-less ``forget(id)`` call."""

        self._active_scope = _merge_scope(self.scope, scope)

    def write(self, event: Event) -> Any:
        scope = _merge_scope(self.scope, self._active_scope, event.scope)
        self._active_scope = scope
        if self.write_fn is not None:
            return self.write_fn(event, scope)
        direct = getattr(self.store, "write", None)
        if callable(direct):
            return direct(event)
        put_records = getattr(self.store, "put_records", None)
        if callable(put_records):
            if self.record_factory is None:
                raise ProviderProtocolError("put_records store requires an injected record_factory")
            record = self.record_factory(event, scope)
            user_id, agent_name = _l1_scope(scope)
            put_records([record], user_id=user_id, agent_name=agent_name)
            return WriteReceipt(event.id, stored=True)
        raise ProviderProtocolError("real store must expose write(event) or put_records(records, ...)")

    def recall(self, query: str, scope: Mapping[str, Any], now: str) -> Sequence[Any]:
        merged_scope = _merge_scope(self.scope, self._active_scope, scope)
        self._active_scope = merged_scope
        if self.recall_fn is not None:
            return self.recall_fn(query, merged_scope, now)
        direct = getattr(self.store, "recall", None)
        if callable(direct):
            return direct(query, merged_scope, now)
        search = getattr(self.store, "search", None)
        if callable(search):
            user_id, agent_name = _l1_scope(merged_scope)
            return search(query, self.top_k, user_id=user_id, agent_name=agent_name)
        raise ProviderProtocolError("real store must expose recall(query, scope, now) or search(query, top_k, ...)")

    def forget(self, id: str) -> Any:
        scope = _merge_scope(self.scope, self._active_scope)
        if self.forget_fn is not None:
            return self.forget_fn(str(id), scope)
        direct = getattr(self.store, "forget", None)
        if callable(direct):
            return direct(str(id))
        delete_records = getattr(self.store, "delete_records", None)
        if callable(delete_records):
            user_id, agent_name = _l1_scope(scope)
            return delete_records([str(id)], user_id=user_id, agent_name=agent_name)
        raise ProviderProtocolError("real store must expose forget(id) or delete_records([id], ...)")


def run_case(
    case: Case | Mapping[str, Any],
    provider: MemoryProvider | Any | None = None,
    *,
    provider_kind: ProviderKind = "real",
    kind: ProviderKind | None = None,
    now: str | None = None,
    recall_limit: int = 10,
    clock: Callable[[], float] | None = None,
) -> CaseResult:
    """Run one case synchronously and return an honest, evidence-bearing result.

    ``unavailable`` is returned when no real provider was injected or the
    injected provider advertises/raises unavailability.  It is never converted
    to a numeric zero and never marked ``pass``.  Unexpected provider
    exceptions are ``error``; deterministic grading mismatches are ``fail``.
    """

    normalized_case = case if isinstance(case, Case) else Case.from_dict(case)
    if kind is not None:
        provider_kind = kind
    effective_now = str(now or normalized_case.now)
    started = _clock_value(clock)
    if provider is not None and provider_kind == "real":
        inferred_kind = getattr(provider, "provider_kind", None)
        if inferred_kind in {"real", "scripted"}:
            provider_kind = inferred_kind
    if provider_kind not in {"real", "scripted"}:
        return _terminal_result(normalized_case, "error", f"unsupported provider kind: {provider_kind!r}", provider_kind, started, clock)
    if provider is None:
        if provider_kind == "scripted":
            provider = ScriptedProvider.from_case(normalized_case)
        else:
            return _terminal_result(normalized_case, "unavailable", "no real memory provider was injected", provider_kind, started, clock)
    try:
        bind_scope = getattr(provider, "bind_scope", None)
        if callable(bind_scope):
            bind_scope(dict(normalized_case.scope))
        if not _provider_is_available(provider):
            return _terminal_result(normalized_case, "unavailable", "memory provider reported unavailable", provider_kind, started, clock)
        stored_ids: list[str] = []
        for source_event in normalized_case.events():
            event = _scoped_event(normalized_case, source_event)
            receipt = provider.write(event)
            record_id, stored = _write_receipt(receipt, event)
            if stored and record_id:
                stored_ids.append(record_id)
        forgotten_ids: list[str] = []
        for record_id in normalized_case.forget_ids:
            provider.forget(record_id)
            forgotten_ids.append(record_id)

        answers: dict[str, str] = {}
        outcomes: list[QuestionOutcome] = []
        returned_records: list[MemoryRecord] = []
        for question in normalized_case.questions:
            raw_records = provider.recall(question.question, dict(question.scope or normalized_case.scope), effective_now)
            records = _normalize_records(raw_records, question.scope or normalized_case.scope)[: max(0, recall_limit)]
            returned_records.extend(records)
            answer = compose_answer(records)
            answers[question.id] = answer
            outcome = _grade_question(normalized_case, question, records, answer)
            outcomes.append(outcome)

        expected_ids = normalized_case.expected_stored_ids
        stored_tuple = tuple(dict.fromkeys(stored_ids))
        write_precision_value = write_precision(stored_tuple, expected_ids)
        write_recall_value = write_recall(stored_tuple, expected_ids)
        relevant_ids = normalized_case.relevant_fact_ids if normalized_case.ability == "extraction" else ()
        retrieved_ids = _relevance_ids(returned_records) if relevant_ids else ()
        extraction_value = recall_at_k(retrieved_ids, relevant_ids, recall_limit) if relevant_ids else None
        failures = _case_failures(outcomes, expected_ids, stored_tuple, write_precision_value, write_recall_value)
        if normalized_case.ability == "extraction" and extraction_value is not None and extraction_value < 1.0:
            failures.append("not all important extraction facts were recalled")
        status = "pass" if not failures else "fail"
        reason = "; ".join(failures)
        evidence_kind = "scripted_oracle" if provider_kind == "scripted" or getattr(provider, "provider_kind", provider_kind) == "scripted" else "provider_returned_records"
        return CaseResult(
            case_id=normalized_case.id,
            ability=normalized_case.ability,
            status=status,
            answers=answers,
            question_outcomes=outcomes,
            latency_ms=_latency_ms(provider_kind, started, clock),
            reason=reason,
            expected_stored_ids=expected_ids,
            stored_ids=stored_tuple,
            forgotten_ids=tuple(forgotten_ids),
            write_precision=write_precision_value,
            write_recall=write_recall_value,
            extraction_recall=extraction_value,
            extraction_relevant_ids=tuple(relevant_ids),
            extraction_retrieved_ids=tuple(retrieved_ids),
            provider_kind=provider_kind,
            evidence_kind=evidence_kind,
        )
    except ProviderUnavailable as exc:
        return _terminal_result(normalized_case, "unavailable", str(exc) or "memory provider unavailable", provider_kind, started, clock)
    except Exception as exc:  # provider failures are errors, never fake scores
        if _looks_unavailable(exc):
            return _terminal_result(normalized_case, "unavailable", str(exc) or "memory provider unavailable", provider_kind, started, clock)
        return _terminal_result(normalized_case, "error", f"provider error: {exc}", provider_kind, started, clock)


def compose_answer(records: Sequence[MemoryRecord]) -> str:
    """Compose a deterministic answer from returned records without an LLM.

    Explicit record ``answer`` values win over snippets.  If a provider only
    returns content, the content snippets are joined.  The runner never looks
    at the question's expected answer while composing.
    """

    cited = [record for record in records if record.cited]
    explicit: list[str] = []
    for record in cited:
        value = str(record.answer or "").strip()
        if value and value not in explicit:
            explicit.append(value)
    if explicit:
        return " ".join(explicit)
    snippets: list[str] = []
    for record in cited:
        value = str(record.content or "").strip()
        if value and value not in snippets:
            snippets.append(value)
    return " ".join(snippets)


def _grade_question(case: Case, question: Question, records: Sequence[MemoryRecord], answer: str) -> QuestionOutcome:
    returned_ids = tuple(record.id for record in records if record.id)
    available_evidence_ids = set(returned_ids)
    for record in records:
        if record.event_id:
            available_evidence_ids.add(record.event_id)
        if record.metadata.get("fact_id"):
            available_evidence_ids.add(str(record.metadata["fact_id"]))
    cited_records = [record for record in records if record.cited and record.id]
    evidence_ids = tuple(record.id for record in cited_records)
    question_scope = _merge_scope(case.scope, question.scope)
    foreign_ids = tuple(record.id for record in cited_records if not scope_matches(question_scope, record.scope))
    superseded_ids = tuple(record.id for record in records if _record_is_superseded(record, question.superseded_values))
    failures: list[str] = []
    cross_session_complete = True
    declared_evidence_complete = True
    if question.expects_abstention:
        correct = is_refusal(answer)
        if not correct:
            failures.append("required refusal was not returned")
    else:
        correct = answer_matches(answer, question.expected, question.acceptable_alternatives)
        if not correct:
            failures.append("answer did not match expected value")
    update_correct: bool | None = None
    if case.ability in {"update", "temporal"} or question.is_update:
        update_correct = update_correctness(answer, question.expected, question.superseded_values, question.acceptable_alternatives) and not superseded_ids
        if not update_correct and question.expects_abstention is False:
            failures.append("current value missing or superseded value returned")
    if not question.expects_abstention:
        if not evidence_ids or not set(evidence_ids).issubset(set(returned_ids)):
            failures.append("answer has no backing returned record")
        missing_declared_evidence = tuple(evidence_id for evidence_id in question.evidence_ids if evidence_id not in available_evidence_ids)
        declared_evidence_complete = not missing_declared_evidence
        if missing_declared_evidence:
            failures.append("declared evidence was not returned")
        if case.ability == "multi_session":
            missing_cross_session = tuple(evidence_id for evidence_id in question.evidence_ids if evidence_id not in available_evidence_ids)
            cross_session_complete = not missing_cross_session
            if missing_cross_session:
                failures.append("multi-session evidence is incomplete")
    if foreign_ids:
        failures.append("answer cites foreign-scope record(s)")
    useful_fact_count = sum(1 for record in cited_records if record.useful)
    return QuestionOutcome(
        question_id=question.id,
        answer=answer,
        correct=bool(correct and not foreign_ids and (update_correct is not False) and (question.expects_abstention or bool(evidence_ids)) and cross_session_complete and declared_evidence_complete),
        expects_abstention=question.expects_abstention,
        evidence_ids=evidence_ids,
        returned_record_ids=returned_ids,
        foreign_evidence_ids=foreign_ids,
        superseded_values=question.superseded_values,
        superseded_evidence_ids=superseded_ids,
        update_correct=update_correct,
        composed_tokens=token_count(answer),
        useful_fact_count=useful_fact_count,
        failure_reasons=tuple(failures),
        metadata={
            "question_type": question.type,
            "scope": question_scope,
            "evidence_scopes": {record.id: dict(record.scope) for record in cited_records},
        },
    )


def _case_failures(
    outcomes: Sequence[QuestionOutcome],
    expected_ids: Sequence[str],
    stored_ids: Sequence[str],
    write_precision_value: float | None,
    write_recall_value: float | None,
) -> list[str]:
    failures: list[str] = []
    failures.extend(f"question {outcome.question_id}: {reason}" for outcome in outcomes for reason in outcome.failure_reasons)
    if expected_ids and write_recall_value != 1.0:
        failures.append("not all expected-stored events were written")
    if expected_ids and stored_ids and write_precision_value is not None and write_precision_value < 1.0:
        failures.append("provider stored an unexpected event")
    if not expected_ids and stored_ids:
        failures.append("provider stored records when none were expected")
    return failures


def _normalize_records(values: Any, requested_scope: Mapping[str, Any]) -> list[MemoryRecord]:
    if values is None:
        return []
    if isinstance(values, Mapping) and "records" in values:
        values = values["records"]
    elif hasattr(values, "records"):
        values = values.records
    if isinstance(values, (str, bytes, Mapping)):
        values = [values]
    if not isinstance(values, Sequence):
        try:
            values = list(values)
        except TypeError as exc:
            raise ProviderProtocolError("provider recall must return a sequence of records") from exc
    return [_coerce_record(value, requested_scope, index) for index, value in enumerate(values) if value is not None]


def _coerce_record(value: Any, requested_scope: Mapping[str, Any], index: int) -> MemoryRecord:
    if isinstance(value, str):
        return MemoryRecord(id=_record_id(value, index), content=value, scope=dict(requested_scope), answer=value)
    if isinstance(value, MemoryRecord):
        scope = dict(value.scope or requested_scope)
        return MemoryRecord(
            id=value.id,
            content=value.content,
            scope=scope,
            answer=value.answer,
            useful=value.useful,
            cited=value.cited,
            metadata=dict(value.metadata),
        )
    if isinstance(value, Mapping):
        data = dict(value)
        raw_scope = data.get("scope")
        scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {key: data[key] for key in ("user_id", "agent_name", "project_id", "session_id") if key in data}
        for camel, snake in (("userId", "user_id"), ("agentName", "agent_name"), ("projectId", "project_id"), ("sessionId", "session_id")):
            if camel in data and snake not in scope:
                scope[snake] = data[camel]
        scope = _merge_scope(requested_scope, scope)
        content = str(data.get("content", data.get("text", data.get("snippet", data.get("summary", data.get("fact", ""))))) or "")
        record_id = str(data.get("id", data.get("record_id", data.get("memory_id", ""))) or _record_id(content, index))
        answer_value = data.get("answer", data.get("value"))
        metadata = dict(data.get("metadata") or {})
        for key in ("event_id", "source_event_id", "fact_id", "source_id", "useful"):
            if key in data and key not in metadata:
                metadata[key] = data[key]
        useful = bool(data.get("useful", metadata.get("useful", True)))
        cited = bool(data.get("cited", data.get("used", True)))
        return MemoryRecord(id=record_id, content=content, scope=scope, answer=None if answer_value is None else str(answer_value), useful=useful, cited=cited, metadata=metadata)
    content = str(getattr(value, "content", getattr(value, "text", getattr(value, "snippet", ""))) or "")
    scope_value = getattr(value, "scope", None)
    scope = _merge_scope(requested_scope, scope_value if isinstance(scope_value, Mapping) else {})
    metadata_value = getattr(value, "metadata", {})
    metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
    record_id = str(getattr(value, "id", getattr(value, "record_id", "")) or _record_id(content, index))
    answer_value = getattr(value, "answer", None)
    return MemoryRecord(
        id=record_id,
        content=content,
        scope=scope,
        answer=None if answer_value is None else str(answer_value),
        useful=bool(getattr(value, "useful", metadata.get("useful", True))),
        cited=bool(getattr(value, "cited", getattr(value, "used", True))),
        metadata=metadata,
    )


def _write_receipt(value: Any, event: Event) -> tuple[str, bool]:
    if isinstance(value, WriteReceipt):
        return value.id or event.id, bool(value.stored)
    if value is None:
        return event.id, True
    if value is False:
        return event.id, False
    if value is True:
        return event.id, True
    if isinstance(value, Mapping):
        stored = bool(value.get("stored", True))
        record_id = str(value.get("id", value.get("record_id", event.id)) or event.id)
        return record_id, stored
    stored = getattr(value, "stored", None)
    if stored is not None:
        record_id = str(getattr(value, "id", event.id) or event.id)
        return record_id, bool(stored)
    if isinstance(value, int):
        return event.id, value > 0
    object_id = getattr(value, "id", None)
    if object_id is not None:
        return str(object_id or event.id), True
    return str(value or event.id), bool(value)


def _provider_is_available(provider: Any) -> bool:
    value = getattr(provider, "available", None)
    if value is None:
        value = getattr(provider, "is_available", True)
    if callable(value):
        value = value()
    return bool(value)


def _looks_unavailable(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, FileNotFoundError, PermissionError)):
        return True
    message = str(exc).casefold()
    return any(marker in message for marker in ("unavailable", "not available", "backend down", "memory backend is disabled"))


def _relevance_ids(records: Sequence[MemoryRecord]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        for value in (record.id, record.event_id, record.metadata.get("fact_id"), record.metadata.get("source_id")):
            if value is not None and str(value) and str(value) not in values:
                values.append(str(value))
    return tuple(values)


def _record_is_superseded(record: MemoryRecord, superseded_values: Sequence[str]) -> bool:
    if bool(record.metadata.get("superseded")) or str(record.metadata.get("status", "")).casefold() in {"superseded", "historical"}:
        return True
    text = f"{record.answer or ''} {record.content}".strip()
    return any(answer_matches(text, value) for value in superseded_values if value)


def _infer_evidence_ids(case: Case, question: Question) -> tuple[str, ...]:
    if question.expected is None:
        return ()
    expected = str(question.expected).casefold()
    exact = tuple(event.id for event in case.events() if event.answer and str(event.answer).casefold() == expected)
    if exact:
        return exact
    return tuple(event.id for event in case.events() if expected in str(event.text).casefold())


def _event_scope(case: Case, event: Event) -> dict[str, Any]:
    return _merge_scope(case.scope, event.scope)


def _scoped_event(case: Case, event: Event) -> Event:
    return Event(
        id=event.id,
        text=event.text,
        session_id=event.session_id,
        timestamp=event.timestamp,
        scope=_event_scope(case, event),
        expected_stored=event.expected_stored,
        answer=event.answer,
        fact_id=event.fact_id,
        memory_type=event.memory_type,
        metadata=dict(event.metadata),
    )


def _event_scope_from_event(event: Event) -> dict[str, Any]:
    return dict(event.scope)


def _merge_scope(*values: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if value:
            merged.update(dict(value))
    return merged


def _l1_scope(scope: Mapping[str, Any]) -> tuple[str | None, str | None]:
    user_id = scope.get("user_id")
    agent_name = scope.get("agent_name")
    return (None if user_id is None else str(user_id), None if agent_name is None else str(agent_name))


def _record_id(content: str, index: int) -> str:
    digest = hashlib.sha256(f"{index}|{content}".encode("utf-8", "strict")).hexdigest()[:16]
    return f"record_{digest}"


def _clock_value(clock: Callable[[], float] | None) -> float:
    return float(clock()) if clock is not None else time.perf_counter()


def _elapsed_ms(started: float, clock: Callable[[], float] | None) -> float:
    ended = _clock_value(clock)
    return max(0.0, (ended - started) * 1000.0)


def _latency_ms(provider_kind: str, started: float, clock: Callable[[], float] | None) -> float:
    # A scripted oracle has no backend operation to time; zero is an honest
    # deterministic sentinel, not a fabricated performance measurement.
    if provider_kind == "scripted" and clock is None:
        return 0.0
    return _elapsed_ms(started, clock)


def _terminal_result(
    case: Case,
    status: Literal["unavailable", "error"],
    reason: str,
    provider_kind: str,
    started: float,
    clock: Callable[[], float] | None,
) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        ability=case.ability,
        status=status,
        reason=reason,
        latency_ms=_latency_ms(provider_kind, started, clock),
        expected_stored_ids=case.expected_stored_ids,
        provider_kind=provider_kind,
        evidence_kind="unavailable" if status == "unavailable" else "provider_error",
    )


__all__ = [
    "MemoryProvider",
    "ProviderKind",
    "ProviderProtocolError",
    "ProviderUnavailable",
    "RealStoreProvider",
    "ScriptedProvider",
    "UnavailableError",
    "UnavailableProvider",
    "WriteReceipt",
    "compose_answer",
    "run_case",
]
