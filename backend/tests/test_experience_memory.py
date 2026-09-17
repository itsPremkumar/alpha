"""Tests for Episodic Experience Memory and Retrieval."""

import json

from agent_workspace.learning.experience.models import ExperienceRecord, OutcomeType
from agent_workspace.learning.experience.retriever import ExperienceRetriever
from agent_workspace.learning.experience.store import ExperienceStore
from agent_workspace.tools.builtins.experience_tool import consult_experience


def test_experience_store_memory_and_disk(tmp_path):
    storage_file = tmp_path / "memory.json"
    store = ExperienceStore(storage_path=storage_file, load_defaults=False)

    rec = ExperienceRecord(
        experience_id="test_exp_1",
        task_goal="Fix race condition in redis lock",
        outcome=OutcomeType.SUCCESS,
        lessons_learned=["Always release locks in finally blocks"],
        pitfalls_to_avoid=["Holding lock across slow I/O calls"],
        tags=["redis", "concurrency", "lock"],
    )
    store.record(rec)

    assert store.get("test_exp_1") is not None
    assert storage_file.exists()

    # Reload from disk
    store_reloaded = ExperienceStore(storage_path=storage_file, load_defaults=False)
    assert store_reloaded.get("test_exp_1") is not None
    loaded_rec = store_reloaded.get("test_exp_1")
    assert loaded_rec.outcome == OutcomeType.SUCCESS
    assert "release locks" in loaded_rec.lessons_learned[0]


def test_experience_retriever():
    store = ExperienceStore(load_defaults=True)
    retriever = ExperienceRetriever(store=store)

    # Query matching the default asyncio deadlock experience
    matches = retriever.retrieve("refactor worker with asyncio and postgres db")
    assert len(matches) > 0
    assert any("async" in m.task_goal.lower() for m in matches)

    # Render lessons prompt
    prompt = retriever.render_lessons_prompt("asyncio database refactor")
    assert "Relevant Past Lessons & Pitfalls" in prompt
    assert "Lessons Learned" in prompt
    assert "Pitfalls to Avoid" in prompt


def test_consult_experience_tool():
    # 1. Query phase
    query_res_str = consult_experience.invoke({
        "query": "asyncio event loop blocking",
        "action": "query",
    })
    query_data = json.loads(query_res_str)
    assert query_data["match_count"] > 0
    assert "markdown_advice" in query_data

    # 2. Record phase
    rec_res_str = consult_experience.invoke({
        "query": "Cache invalidation on tenant update",
        "action": "record",
        "outcome": "success",
        "lessons": ["Invalidate multi-tenant cache keys using namespace patterns"],
        "pitfalls": ["Flushing the entire Redis cache affects other tenants"],
    })
    rec_data = json.loads(rec_res_str)
    assert rec_data["status"] == "recorded"
    assert "experience_id" in rec_data
