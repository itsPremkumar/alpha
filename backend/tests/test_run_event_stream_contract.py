"""Conformance tests for the documented run event stream contract."""

from __future__ import annotations

import ast
import importlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from alpha.runtime.events.catalog import (
    FIXED_RUN_EVENT_DEFINITIONS,
    JOURNAL_RUN_EVENT_DEFINITIONS,
    MIDDLEWARE_EVENT_PATTERN,
    MIDDLEWARE_EVENT_TAG_MAX_LENGTH,
    MIDDLEWARE_EVENT_TAGS,
    MIDDLEWARE_TOOL_PROMOTION_TAG,
    RUN_EVENT_CATEGORY_MAX_LENGTH,
    RUN_EVENT_TYPE_MAX_LENGTH,
    SUBAGENT_RUN_EVENT_DEFINITIONS,
    WORKSPACE_RUN_EVENT_DEFINITIONS,
    RunEventDefinition,
    RunEventPattern,
)
from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.journal import RunJournal
from alpha.subagents.step_events import SUBAGENT_STEP_MAX_CHARS, capture_step_message, subagent_run_event

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "run_event_stream_contract.json"


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _contract_events() -> dict[str, dict]:
    return {event["event_type"]: event for event in _load_contract()["events"]}


def _assert_schema_valid(schema: dict | bool, instance: object) -> None:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    assert not errors, "; ".join(error.message for error in errors)


def _assert_fixed_event_valid(event: dict, *, persisted: bool = False) -> None:
    contract_event = _contract_events()[event["event_type"]]
    assert event["category"] == contract_event["category"]
    _assert_schema_valid(contract_event["content_schema"], event["content"])
    _assert_schema_valid(contract_event["metadata_schema"], event.get("metadata", {}))
    if persisted:
        _assert_schema_valid(_load_contract()["record_schema"], event)


def _make_llm_response(content: str = "answer", usage: dict | None = None) -> LLMResult:
    message = AIMessage(
        content=content,
        id=f"msg-{uuid4()}",
        response_metadata={"model_name": "test-model"},
        usage_metadata=usage,
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _subagent_batch() -> list[dict]:
    chunks = [
        {"type": "task_started", "task_id": "call-batch", "description": "research"},
        {
            "type": "task_running",
            "task_id": "call-batch",
            "message": {
                "type": "ai",
                "content": "searching",
                "tool_calls": [{"name": "web_search", "args": {"query": "alpha"}}],
            },
            "message_index": 1,
        },
        {"type": "task_completed", "task_id": "call-batch", "result": "done"},
    ]
    events = [subagent_run_event(chunk) for chunk in chunks]
    assert all(event is not None for event in events)
    return [{"thread_id": "thread-batch", "run_id": "run-batch", **event} for event in events if event is not None]


async def _persist_subagent_batch(store) -> list[dict]:
    await store.put_batch(_subagent_batch())
    return await store.list_events("thread-batch", "run-batch")


async def _record_run_end(store) -> dict:
    journal = RunJournal("run-output", "thread-output", store, flush_threshold=100)
    journal.on_chain_end(
        {"messages": [AIMessage(content="final answer", id="final-message")]},
        run_id=uuid4(),
        parent_run_id=None,
    )
    await journal.flush()
    events = await store.list_events("thread-output", "run-output", event_types=["run.end"])
    assert len(events) == 1
    return events[0]


def test_contract_and_runtime_catalog_have_the_same_fixed_events():
    contract = _load_contract()
    contract_types = [event["event_type"] for event in contract["events"]]
    runtime_types = [definition.event_type for definition in FIXED_RUN_EVENT_DEFINITIONS]
    contract_pairs = {(event["event_type"], event["category"]) for event in contract["events"]}
    runtime_pairs = {(definition.event_type, definition.category) for definition in FIXED_RUN_EVENT_DEFINITIONS}

    assert len(set(contract_types)) == len(contract_types)
    assert len(set(runtime_types)) == len(runtime_types)
    assert contract_pairs == runtime_pairs
    assert set(contract["categories"]) == {definition.category for definition in FIXED_RUN_EVENT_DEFINITIONS} | {MIDDLEWARE_EVENT_PATTERN.category}

    event_type_schema = contract["record_schema"]["properties"]["event_type"]
    category_schema = contract["record_schema"]["properties"]["category"]
    middleware_pattern = contract["dynamic_event_patterns"][0]
    assert event_type_schema["maxLength"] == RUN_EVENT_TYPE_MAX_LENGTH
    assert category_schema["maxLength"] == RUN_EVENT_CATEGORY_MAX_LENGTH
    assert middleware_pattern["event_type_schema"]["maxLength"] == RUN_EVENT_TYPE_MAX_LENGTH
    assert middleware_pattern["tag_schema"]["maxLength"] == MIDDLEWARE_EVENT_TAG_MAX_LENGTH

    from alpha.persistence.models.run_event import RunEventRow

    assert RunEventRow.__table__.c.event_type.type.length == RUN_EVENT_TYPE_MAX_LENGTH
    assert RunEventRow.__table__.c.category.type.length == RUN_EVENT_CATEGORY_MAX_LENGTH


@pytest.mark.parametrize(
    ("definition_type", "kwargs"),
    [
        (RunEventDefinition, {"event_type": "test.event"}),
        (RunEventPattern, {"pattern": "test:{tag}", "prefix": "test:"}),
    ],
)
def test_runtime_catalog_rejects_categories_that_do_not_fit_persistence(definition_type, kwargs):
    assert definition_type(category="x" * RUN_EVENT_CATEGORY_MAX_LENGTH, **kwargs).category

    for invalid_category in ("", "x" * (RUN_EVENT_CATEGORY_MAX_LENGTH + 1)):
        with pytest.raises(ValueError, match="category"):
            definition_type(category=invalid_category, **kwargs)


@pytest.mark.parametrize(
    "relative_path",
    [
        "persistence/models/run_event.py",
        "workspace_changes/types.py",
    ],
)
def test_lower_level_run_event_modules_do_not_import_runtime(relative_path):
    module_path = REPO_ROOT / "backend" / "packages" / "harness" / "alpha" / relative_path
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None]
    imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)

    assert not [module for module in imports if module == "alpha.runtime" or module.startswith("alpha.runtime.")]


def test_legacy_aliases_are_read_only_and_outside_the_current_catalog():
    contract = _load_contract()
    aliases = {alias["event_type"]: alias for alias in contract["legacy_event_aliases"]}
    current_types = {definition.event_type for definition in FIXED_RUN_EVENT_DEFINITIONS}

    assert set(aliases) == {"ai_message"}
    assert aliases["ai_message"]["canonical_event_type"] == "llm.ai.response"
    assert aliases["ai_message"]["produced_by_current_runtime"] is False
    assert "/messages/page" in aliases["ai_message"]["compatibility_scope"]
    assert "legacy /messages endpoint" in aliases["ai_message"]["known_limitations"]
    assert set(aliases).isdisjoint(current_types)


def test_record_envelope_accepts_every_json_content_type():
    schema = _load_contract()["record_schema"]
    envelope = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "seq": 1,
        "event_type": "run.end",
        "category": "outputs",
        "metadata": {},
        "created_at": "2026-07-21T00:00:00+00:00",
    }

    for content in ("text", {"key": "value"}, ["value"], 1, 1.5, True, None):
        _assert_schema_valid(schema, {**envelope, "content": content})


def test_contract_schemas_are_valid_json_schema():
    contract = _load_contract()
    Draft202012Validator.check_schema(contract["record_schema"])
    for event in contract["events"]:
        Draft202012Validator.check_schema(event["content_schema"])
        Draft202012Validator.check_schema(event["metadata_schema"])
    for pattern in contract["dynamic_event_patterns"]:
        Draft202012Validator.check_schema(pattern["event_type_schema"])
        Draft202012Validator.check_schema(pattern["tag_schema"])
        Draft202012Validator.check_schema(pattern["content_schema"])
        Draft202012Validator.check_schema(pattern["metadata_schema"])


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_non_database_stores_return_contract_records(backend, tmp_path):
    if backend == "memory":
        store = MemoryRunEventStore()
    else:
        from alpha.runtime.events.store.jsonl import JsonlRunEventStore

        store = JsonlRunEventStore(base_dir=tmp_path / "events")

    record = await store.put(
        thread_id="thread-1",
        run_id="run-1",
        event_type="context:memory",
        category="context",
        content={"content_sha256": "a" * 64},
        metadata={},
    )

    _assert_fixed_event_valid(record, persisted=True)
    assert record["seq"] == 1


@pytest.mark.anyio
async def test_database_store_returns_contract_record_with_backend_fields(tmp_path):
    from alpha.persistence.engine import close_engine, get_session_factory, init_engine
    from alpha.runtime.events.store.db import DbRunEventStore

    url = f"sqlite+aiosqlite:///{tmp_path / 'events.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        store = DbRunEventStore(get_session_factory())
        record = await store.put(
            thread_id="thread-1",
            run_id="run-1",
            event_type="context:memory",
            category="context",
            content={"content_sha256": "a" * 64},
            metadata={},
        )

        _assert_fixed_event_valid(record, persisted=True)
        assert "user_id" in record
        assert record["metadata"]["content_is_json"] is True
    finally:
        await close_engine()


@pytest.mark.anyio
async def test_run_end_backend_storage_semantics_match_contract(tmp_path):
    from alpha.persistence.engine import close_engine, get_session_factory, init_engine
    from alpha.runtime.events.store.db import DbRunEventStore
    from alpha.runtime.events.store.jsonl import JsonlRunEventStore

    contract_event = _contract_events()["run.end"]
    assert set(contract_event["storage_semantics"]) == {"memory", "jsonl", "database"}

    memory_event = await _record_run_end(MemoryRunEventStore())
    jsonl_event = await _record_run_end(JsonlRunEventStore(base_dir=tmp_path / "events"))

    url = f"sqlite+aiosqlite:///{tmp_path / 'run-output.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        database_event = await _record_run_end(DbRunEventStore(get_session_factory()))
    finally:
        await close_engine()

    for event in (memory_event, jsonl_event, database_event):
        _assert_fixed_event_valid(event, persisted=True)

    assert isinstance(memory_event["content"]["messages"][0], AIMessage)
    assert isinstance(jsonl_event["content"]["messages"][0], str)
    assert isinstance(database_event["content"]["messages"][0], str)
    assert "final answer" in jsonl_event["content"]["messages"][0]
    assert "final answer" in database_event["content"]["messages"][0]


@pytest.mark.anyio
async def test_run_journal_observed_events_exactly_match_its_catalog():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)

    root_run_id = uuid4()
    llm_run_id = uuid4()
    journal.on_chain_start(
        {"name": "root"},
        {},
        run_id=root_run_id,
        parent_run_id=None,
        tags=["lead_agent"],
        metadata={"langgraph_step": 1},
    )
    journal.on_chat_model_start(
        {},
        [[HumanMessage(content="question", id="human-1")]],
        run_id=llm_run_id,
        tags=["lead_agent"],
    )
    journal.on_llm_end(
        _make_llm_response("answer", usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}),
        run_id=llm_run_id,
        parent_run_id=None,
        tags=["lead_agent"],
    )
    journal.on_tool_end(
        ToolMessage(content="tool result", tool_call_id="call-1", name="web_search", id="tool-1"),
        run_id=uuid4(),
    )
    journal.on_llm_error(RuntimeError("model failed"), run_id=uuid4())
    journal.on_chain_error(ValueError("run failed"), run_id=uuid4())
    journal.on_chain_end({"messages": []}, run_id=root_run_id, parent_run_id=None)
    journal.record_memory_context(content_sha256="a" * 64)
    await journal.flush()

    events = await store.list_events("thread-1", "run-1")
    expected_types = {definition.event_type for definition in JOURNAL_RUN_EVENT_DEFINITIONS}

    assert {event["event_type"] for event in events} == expected_types
    for event in events:
        _assert_fixed_event_valid(event, persisted=True)


@pytest.mark.anyio
@pytest.mark.parametrize("tag", MIDDLEWARE_EVENT_TAGS)
async def test_dynamic_middleware_event_matches_pattern_contract(tag):
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)
    journal.record_middleware(
        tag,
        name="GuardrailMiddleware",
        hook="wrap_tool_call",
        action="deny",
        changes={"reason": "policy"},
    )
    await journal.flush()

    event = (await store.list_events("thread-1", "run-1"))[0]
    pattern = _load_contract()["dynamic_event_patterns"][0]

    assert pattern["pattern"] == MIDDLEWARE_EVENT_PATTERN.pattern
    assert set(pattern["known_tags"]) == set(MIDDLEWARE_EVENT_TAGS)
    assert event["event_type"] == MIDDLEWARE_EVENT_PATTERN.event_type(tag)
    assert event["category"] == MIDDLEWARE_EVENT_PATTERN.category == pattern["category"]
    _assert_schema_valid(pattern["event_type_schema"], event["event_type"])
    _assert_schema_valid(pattern["tag_schema"], tag)
    _assert_schema_valid(pattern["content_schema"], event["content"])
    _assert_schema_valid(pattern["metadata_schema"], event["metadata"])


@pytest.mark.parametrize("tag", ["", "x" * (MIDDLEWARE_EVENT_TAG_MAX_LENGTH + 1)])
def test_dynamic_middleware_event_rejects_tags_that_do_not_fit_persistence(tag):
    journal = RunJournal("run-1", "thread-1", MemoryRunEventStore(), flush_threshold=100)

    with pytest.raises(ValueError):
        journal.record_middleware(
            tag,
            name="CustomMiddleware",
            hook="after_model",
            action="record",
            changes={},
        )


def test_tool_promotion_tag_is_declared_and_fits_the_persisted_event_type():
    pattern = _load_contract()["dynamic_event_patterns"][0]

    assert MIDDLEWARE_TOOL_PROMOTION_TAG == "tool_promotion"
    assert MIDDLEWARE_TOOL_PROMOTION_TAG in MIDDLEWARE_EVENT_TAGS
    assert MIDDLEWARE_TOOL_PROMOTION_TAG in pattern["known_tags"]
    assert len(MIDDLEWARE_EVENT_PATTERN.event_type(MIDDLEWARE_TOOL_PROMOTION_TAG)) <= RUN_EVENT_TYPE_MAX_LENGTH


def test_subagent_observed_events_exactly_match_its_catalog_and_payloads():
    long_result = "r" * (SUBAGENT_STEP_MAX_CHARS + 1)
    long_error = "e" * (SUBAGENT_STEP_MAX_CHARS + 1)
    cases = [
        {"type": "task_started", "task_id": "call-1", "description": "research"},
        {
            "type": "task_running",
            "task_id": "call-1",
            "message": {
                "type": "ai",
                "content": "searching",
                "tool_calls": [{"name": "web_search", "args": {"query": "alpha"}}],
            },
            "message_index": 1,
        },
        {
            "type": "task_running",
            "task_id": "call-1",
            "message": {"type": "tool", "name": "web_search", "content": "result"},
            "message_index": 2,
        },
        {
            "type": "task_completed",
            "task_id": "call-1",
            "result": "done",
            "model_name": "test-model",
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        },
        {"type": "task_failed", "task_id": "call-2", "error": "boom"},
        {"type": "task_cancelled", "task_id": "call-3"},
        {"type": "task_timed_out", "task_id": "call-4", "error": "timed out"},
        {"type": "task_completed", "task_id": "call-5", "result": long_result},
        {"type": "task_failed", "task_id": "call-6", "error": long_error},
    ]

    records = [subagent_run_event(case) for case in cases]
    assert all(record is not None for record in records)
    typed_records = [record for record in records if record is not None]
    expected_types = {definition.event_type for definition in SUBAGENT_RUN_EVENT_DEFINITIONS}

    assert {record["event_type"] for record in typed_records} == expected_types
    for record in typed_records:
        _assert_fixed_event_valid(record)

    ai_step, tool_step = typed_records[1]["content"], typed_records[2]["content"]
    completed, failed = typed_records[3]["content"], typed_records[4]["content"]
    timed_out = typed_records[6]["content"]
    truncated_result, truncated_error = typed_records[7]["content"], typed_records[8]["content"]
    assert ai_step["tool_calls"][0]["name"] == "web_search"
    assert tool_step["tool_name"] == "web_search"
    assert completed["result"] == "done"
    assert completed["model_name"] == "test-model"
    assert completed["usage"]["total_tokens"] == 7
    assert failed["error"] == "boom"
    assert timed_out["error"] == "timed out"
    assert len(truncated_result["result"]) == SUBAGENT_STEP_MAX_CHARS
    assert truncated_result["result_truncated"] is True
    assert len(truncated_error["error"]) == SUBAGENT_STEP_MAX_CHARS
    assert truncated_error["error_truncated"] is True
    assert {record["content"]["status"] for record in typed_records[3:]} == {
        "completed",
        "failed",
        "cancelled",
        "timed_out",
    }


def test_captured_subagent_message_survives_task_running_conversion():
    captured: list[dict] = []
    assert capture_step_message(
        AIMessage(
            content="searching",
            id="ai-step-1",
            tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "alpha"}}],
        ),
        captured,
        set(),
    )

    event = subagent_run_event(
        {
            "type": "task_running",
            "task_id": "task-1",
            "message": captured[0],
            "message_index": 0,
        }
    )

    assert event is not None
    assert event["event_type"] == "subagent.step"
    assert event["content"]["task_id"] == "task-1"
    assert event["content"]["message_index"] == 0
    assert event["content"]["text"] == "searching"
    assert event["content"]["tool_calls"] == [{"name": "web_search", "args": {"query": "alpha"}}]
    _assert_fixed_event_valid(event)


@pytest.mark.parametrize(
    "chunk",
    [
        {"type": "task_started", "description": "missing task id"},
        {"type": "task_started", "task_id": "", "description": "empty task id"},
        {"type": "task_started", "task_id": "call-1", "description": 42},
        {"type": "task_running", "task_id": "call-1", "message": {"type": "ai"}},
        {"type": "task_running", "task_id": "call-1", "message": {"type": "ai"}, "message_index": -1},
        {"type": "task_running", "task_id": "call-1", "message": {"type": "ai"}, "message_index": True},
        {"type": "task_running", "task_id": "call-1", "message": "not-an-object", "message_index": 0},
        {"type": "task_completed"},
    ],
)
def test_subagent_producer_rejects_chunks_missing_contract_fields(chunk):
    assert subagent_run_event(chunk) is None


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_subagent_batch_round_trip_matches_contract_for_non_database_stores(backend, tmp_path):
    if backend == "memory":
        store = MemoryRunEventStore()
    else:
        from alpha.runtime.events.store.jsonl import JsonlRunEventStore

        store = JsonlRunEventStore(base_dir=tmp_path / "subagent-events")

    records = await _persist_subagent_batch(store)

    assert [record["event_type"] for record in records] == ["subagent.start", "subagent.step", "subagent.end"]
    for record in records:
        _assert_fixed_event_valid(record, persisted=True)


@pytest.mark.anyio
async def test_subagent_batch_round_trip_matches_contract_for_database_store(tmp_path):
    from alpha.persistence.engine import close_engine, get_session_factory, init_engine
    from alpha.runtime.events.store.db import DbRunEventStore

    url = f"sqlite+aiosqlite:///{tmp_path / 'subagent-events.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        records = await _persist_subagent_batch(DbRunEventStore(get_session_factory()))
    finally:
        await close_engine()

    assert [record["event_type"] for record in records] == ["subagent.start", "subagent.step", "subagent.end"]
    for record in records:
        _assert_fixed_event_valid(record, persisted=True)


@pytest.mark.anyio
async def test_workspace_change_producer_matches_catalog_and_payload(monkeypatch, tmp_path):
    from alpha.workspace_changes import WorkspaceRoot, scan_workspace_roots
    from alpha.workspace_changes import recorder as recorder_module

    workspace = tmp_path / "workspace"
    outputs = tmp_path / "outputs"
    workspace.mkdir()
    outputs.mkdir()
    roots = [
        WorkspaceRoot("workspace", workspace, "/mnt/user-data/workspace"),
        WorkspaceRoot("outputs", outputs, "/mnt/user-data/outputs"),
    ]
    before = scan_workspace_roots(roots)
    (workspace / "report.md").write_text("# Report\n", encoding="utf-8")
    monkeypatch.setattr(recorder_module, "build_thread_workspace_roots", lambda *_args, **_kwargs: roots)

    store = MemoryRunEventStore()
    record = await recorder_module.record_workspace_changes(store, "thread-1", "run-1", before)

    assert record is not None
    assert {record["event_type"]} == {definition.event_type for definition in WORKSPACE_RUN_EVENT_DEFINITIONS}
    _assert_fixed_event_valid(record, persisted=True)


def test_known_gaps_do_not_reclassify_current_events_as_missing():
    contract = _load_contract()
    gap_ids = {gap["id"] for gap in contract["known_gaps"]}
    current_types = {definition.event_type for definition in FIXED_RUN_EVENT_DEFINITIONS}

    assert {"tool-call-intent", "terminal-run-status"}.issubset(gap_ids)
    assert all(gap.get("event_type") not in current_types for gap in contract["known_gaps"])


# ---------------------------------------------------------------------------
# Declared-type -> live-emitter guard.
#
# The contract is the document a reader trusts to say which events exist. The
# failure this section exists to prevent is a contract declaring an event type
# that no production code ever writes: a reader looks for the events, finds
# none, and concludes the thing they are debugging never happened. Parity
# against `runtime/events/catalog.py` alone cannot catch that, because the
# catalog is a list of *names* and would happily gain a name nothing emits.
#
# Two independent checks:
#   1. every declared type has a proof here that drives the REAL production
#      producer and returns a persisted, contract-valid record;
#   2. every declared ``producer`` symbol still resolves in production code, so
#      a retracted or renamed emitter cannot leave the contract naming a ghost.
# ---------------------------------------------------------------------------

_PRODUCTION_PACKAGE_ROOTS = (
    (REPO_ROOT / "backend" / "packages" / "harness", "alpha"),
    (REPO_ROOT / "backend", "app"),
)

_PRODUCER_SYMBOL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")


@lru_cache(maxsize=1)
def _production_definition_index() -> dict[str, tuple[str, ...]]:
    """Map a top-level ``def``/``class`` name to the production modules defining it.

    AST-only: this indexes the repository and imports nothing, so a guard
    assertion can never be satisfied by a module that merely happens to be
    importable.
    """
    index: dict[str, list[str]] = {}
    for root, package_name in _PRODUCTION_PACKAGE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            try:
                relative = path.relative_to(root)
            except ValueError:  # pragma: no cover - roots are disjoint
                continue
            if relative.parts[0] not in {package_name, f"{package_name}.py"}:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if not parts:
                continue
            module_name = ".".join(parts)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:  # pragma: no cover - all production code parses
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    index.setdefault(node.name, []).append(module_name)
    return {name: tuple(sorted(set(modules))) for name, modules in index.items()}


def _producer_symbol(producer: str) -> str:
    """The dotted symbol a contract ``producer`` field names.

    Producers carry a call hint after the symbol (``RunJournal.on_llm_end()``,
    ``subagent_run_event(task_started)``, ``RunJournal.record_memory_context()
    from DynamicContextMiddleware``); only the leading dotted path is the
    symbol, and taking it mechanically is the point — the hint prose is
    documentation, not something to keep in sync.
    """
    match = _PRODUCER_SYMBOL_RE.match(producer.strip())
    assert match is not None, f"producer does not start with a dotted symbol: {producer!r}"
    return match.group(1)


def _resolve_producer_symbol(symbol: str) -> Any:
    """Resolve a contract producer symbol against production code, or ``None``."""
    parts = symbol.split(".")
    index = _production_definition_index()
    # Longest head first: ``RunJournal.on_chain_start`` -> class RunJournal,
    # ``subagent_run_event`` -> the whole symbol is the head.
    splits = [split for split in range(len(parts) - 1, 0, -1)] + [len(parts)]
    for split in splits:
        head, tail = ".".join(parts[:split]), parts[split:]
        for module_name in index.get(head, ()):
            obj: Any = getattr(importlib.import_module(module_name), head, None)
            if obj is None:
                continue
            for attribute in tail:
                obj = getattr(obj, attribute, None)
                if obj is None:
                    break
            if obj is not None:
                return obj
    # Otherwise the first segment is a package: ``workspace_changes.foo`` is
    # ``alpha.workspace_changes.foo`` re-exported from the package __init__.
    for package_root in ("alpha", "app"):
        try:
            module = importlib.import_module(f"{package_root}.{'.'.join(parts[:-1])}")
        except ImportError:
            continue
        found = getattr(module, parts[-1], None)
        if found is not None:
            return found
    return None


def _new_journal() -> tuple[RunJournal, MemoryRunEventStore]:
    store = MemoryRunEventStore()
    return RunJournal("run-1", "thread-1", store, flush_threshold=100), store


async def _only_event(store: MemoryRunEventStore, event_type: str) -> dict:
    events = await store.list_events("thread-1", "run-1", event_types=[event_type])
    assert len(events) == 1, f"expected exactly one {event_type}, got {[e['event_type'] for e in events]}"
    return events[0]


async def _persist_subagent_chunk(chunk: dict) -> dict:
    record = subagent_run_event(chunk)
    assert record is not None, f"production producer rejected its own lifecycle chunk: {chunk!r}"
    store = MemoryRunEventStore()
    await store.put(thread_id="thread-1", run_id="run-1", **record)
    return (await store.list_events("thread-1", "run-1"))[0]


async def _emit_journal(callback) -> dict:
    journal, store = _new_journal()
    callback(journal)
    await journal.flush()
    return store


async def _proof_run_start(_tmp_path: Path) -> dict:
    return await _only_event(await _emit_journal(lambda j: j.on_chain_start({"name": "root"}, {}, run_id=uuid4(), parent_run_id=None, tags=["lead_agent"])), "run.start")


async def _proof_run_end(_tmp_path: Path) -> dict:
    journal, store = _new_journal()
    journal.on_chain_end({"messages": [AIMessage(content="done", id="final")]}, run_id=uuid4(), parent_run_id=None)
    await journal.flush()
    return await _only_event(store, "run.end")


async def _proof_run_error(_tmp_path: Path) -> dict:
    return await _only_event(await _emit_journal(lambda j: j.on_chain_error(ValueError("boom"), run_id=uuid4())), "run.error")


async def _proof_llm_human_input(_tmp_path: Path) -> dict:
    journal, store = _new_journal()
    journal.on_chat_model_start({}, [[HumanMessage(content="question", id="human-1")]], run_id=uuid4(), tags=["lead_agent"])
    await journal.flush()
    return await _only_event(store, "llm.human.input")


async def _proof_llm_ai_response(_tmp_path: Path) -> dict:
    journal, store = _new_journal()
    journal.on_llm_end(
        _make_llm_response("answer", usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}),
        run_id=uuid4(),
        parent_run_id=None,
        tags=["lead_agent"],
    )
    await journal.flush()
    return await _only_event(store, "llm.ai.response")


async def _proof_llm_tool_result(_tmp_path: Path) -> dict:
    journal, store = _new_journal()
    journal.on_tool_end(ToolMessage(content="result", tool_call_id="call-1", name="web_search", id="tool-1"), run_id=uuid4())
    await journal.flush()
    return await _only_event(store, "llm.tool.result")


async def _proof_llm_error(_tmp_path: Path) -> dict:
    return await _only_event(await _emit_journal(lambda j: j.on_llm_error(RuntimeError("model failed"), run_id=uuid4())), "llm.error")


async def _proof_context_memory(_tmp_path: Path) -> dict:
    return await _only_event(await _emit_journal(lambda j: j.record_memory_context(content_sha256="a" * 64)), "context:memory")


async def _proof_subagent_start(_tmp_path: Path) -> dict:
    return await _persist_subagent_chunk({"type": "task_started", "task_id": "call-1", "description": "research"})


async def _proof_subagent_step(_tmp_path: Path) -> dict:
    return await _persist_subagent_chunk(
        {
            "type": "task_running",
            "task_id": "call-1",
            "message": {"type": "ai", "content": "searching", "tool_calls": [{"name": "web_search", "args": {"query": "alpha"}}]},
            "message_index": 1,
        }
    )


async def _proof_subagent_end(_tmp_path: Path) -> dict:
    return await _persist_subagent_chunk({"type": "task_completed", "task_id": "call-1", "result": "done", "model_name": "test-model", "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}})


async def _proof_workspace_changes(tmp_path: Path) -> dict:
    from alpha.workspace_changes import WorkspaceRoot, scan_workspace_roots
    from alpha.workspace_changes import recorder as recorder_module

    workspace = tmp_path / "workspace"
    outputs = tmp_path / "outputs"
    workspace.mkdir()
    outputs.mkdir()
    roots = [
        WorkspaceRoot("workspace", workspace, "/mnt/user-data/workspace"),
        WorkspaceRoot("outputs", outputs, "/mnt/user-data/outputs"),
    ]
    before = scan_workspace_roots(roots)
    (workspace / "report.md").write_text("# Report\n", encoding="utf-8")

    store = MemoryRunEventStore()
    with patch.object(recorder_module, "build_thread_workspace_roots", lambda *_a, **_k: roots):
        record = await recorder_module.record_workspace_changes(store, "thread-1", "run-1", before)

    assert record is not None, "production recorder reported no change for a file that was written"
    return record


#: One entry per event type the contract declares. A key can only be added once a
#: real production producer demonstrably writes that record, which is what makes
#: the completeness assertion below a guard rather than a restatement of the
#: contract.
_EMITTER_PROOFS: dict[str, Any] = {
    "run.start": _proof_run_start,
    "run.end": _proof_run_end,
    "run.error": _proof_run_error,
    "llm.human.input": _proof_llm_human_input,
    "llm.ai.response": _proof_llm_ai_response,
    "llm.tool.result": _proof_llm_tool_result,
    "llm.error": _proof_llm_error,
    "context:memory": _proof_context_memory,
    "subagent.start": _proof_subagent_start,
    "subagent.step": _proof_subagent_step,
    "subagent.end": _proof_subagent_end,
    "workspace_changes": _proof_workspace_changes,
}


@pytest.mark.anyio
async def test_every_declared_event_type_has_a_live_production_emitter(tmp_path):
    """A declared event type with no emitter is a contract that lies by omission.

    A reader who trusts the contract looks for the events, finds none, and
    concludes the thing being debugged never happened. Catalog parity cannot
    catch that, because the catalog is a list of names.
    """
    contract = _load_contract()
    declared = [event["event_type"] for event in contract["events"]]

    assert len(set(declared)) == len(declared), "contract declares a duplicate event_type"
    assert set(_EMITTER_PROOFS) == set(declared), (
        "contract declares event types with no live production emitter, or a proof exists for a type the contract does not declare. "
        f"declared-but-unproven: {sorted(set(declared) - set(_EMITTER_PROOFS))}; "
        f"proven-but-undeclared: {sorted(set(_EMITTER_PROOFS) - set(declared))}"
    )

    for event_type in declared:
        record = await _EMITTER_PROOFS[event_type](tmp_path)
        assert record["event_type"] == event_type
        _assert_fixed_event_valid(record, persisted=True)


def test_every_declared_producer_symbol_resolves_in_production_code():
    """A retracted or renamed emitter must not leave the contract naming a ghost."""
    unresolved: dict[str, str] = {}
    for event_type, event in _contract_events().items():
        symbol = _producer_symbol(event["producer"])
        if _resolve_producer_symbol(symbol) is None:
            unresolved[event_type] = symbol
    for pattern in _load_contract()["dynamic_event_patterns"]:
        symbol = _producer_symbol(pattern["producer"])
        if _resolve_producer_symbol(symbol) is None:
            unresolved[pattern["pattern"]] = symbol

    assert not unresolved, f"contract names producers no production module defines: {unresolved}"


def test_emitter_proofs_are_keyed_by_the_contract_not_by_the_catalog():
    """The proof table must be the contract's own list, restated in code.

    Deriving the expected set from the catalog would make the guard circular:
    adding a name to the catalog would satisfy the guard without any emitter.
    """
    contract_types = {event["event_type"] for event in _load_contract()["events"]}
    assert set(_EMITTER_PROOFS) == contract_types
    assert set(_EMITTER_PROOFS) == {definition.event_type for definition in FIXED_RUN_EVENT_DEFINITIONS}


def test_subagent_events_declare_the_stream_mode_they_actually_require():
    """A declared event type must not over-promise.

    The three subagent events have exactly one production emitter, reachable
    only through the parent's ``stream_mode=custom`` consumer. The contract said
    nothing about that, so a reader who trusted it would look for the events,
    find none on a run that genuinely delegated, and conclude no delegation
    happened. The precondition is now stated per event, and this test keeps it
    there.
    """
    events = _contract_events()
    for event_type in ("subagent.start", "subagent.step", "subagent.end"):
        precondition = events[event_type]["emission_precondition"]
        assert precondition["requires_stream_mode"] == "custom", event_type
        assert "normalize_stream_modes" in precondition["notes"], event_type

    # The stated precondition must be the real one, not a convenient claim.
    from alpha.runtime.stream_modes import normalize_stream_modes, to_langgraph_stream_modes

    assert normalize_stream_modes(None) == ["values"]
    assert "custom" not in to_langgraph_stream_modes(None)
    assert "custom" in to_langgraph_stream_modes(["values", "custom"])


def test_contract_names_the_surfaces_that_are_authoritative_for_delegation():
    """An operator must be told where to look instead of the missing events.

    ``subagent.*`` is conditional evidence. The unconditional record of a
    delegation is the thread-state ledger plus the terminal ``task`` ToolMessage,
    and the contract has to say so in the same document that declares the
    conditional events, or a reader has no way to tell which one to trust.
    """
    contract = _load_contract()
    evidence = contract["delegation_evidence"]

    authoritative = {entry["surface"] for entry in evidence["authoritative_and_unconditional"]}
    assert "ThreadState.delegations" in authoritative
    assert "values.delegations" in evidence["authoritative_and_unconditional"][0]["read_via"]

    conditional = {entry["surface"] for entry in evidence["conditional"]}
    assert conditional == {"subagent.start / subagent.step / subagent.end"}
    assert all(entry["requires_stream_mode"] == "custom" for entry in evidence["conditional"])

    # The gap is recorded rather than buried: it names the affected types, the
    # measured proof, and where the fix belongs (not in this document).
    gap = next(gap for gap in contract["known_gaps"] if gap["id"] == "subagent-event-stream-mode-precondition")
    assert gap["affected_event_types"] == ["subagent.start", "subagent.step", "subagent.end"]
    assert gap["status"] == "partial"
    assert "test_subagent_events_e2e.py" in gap["notes"]
    assert "runtime/runs/worker.py" in gap["notes"]
    # Delegation keeps exactly one lifecycle owner; the event stream is
    # downstream evidence and must never be described as a second one.
    assert "not_a_second_lifecycle_owner" in gap
    assert "subagents/executor.py" in gap["not_a_second_lifecycle_owner"]
