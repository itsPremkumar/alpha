import json
from pathlib import Path

from alpha.memory.dreaming.phases import (
    MemorySignal,
    run_deep_sleep_phase,
    run_dream_cycle,
    run_light_sleep_phase,
    run_rem_sleep_phase,
)
from alpha.memory.dreaming.store import DreamStore
from alpha.tools.builtins.dreaming_tool import consolidate_memory_dream


def test_dreaming_phases(tmp_path: Path):
    signals = [
        MemorySignal(content="Always run tests before committing code", category="dev_rules"),
        MemorySignal(content="always run tests before committing code", category="dev_rules"),  # duplicate
        MemorySignal(content="Syntax error in parser module when receiving empty string", category="parser"),
        MemorySignal(content="Assertion failure in parser module on null byte", category="parser"),
    ]

    # 1. Light Sleep
    cleaned = run_light_sleep_phase(signals)
    assert len(cleaned) == 3  # Duplicate eliminated

    # 2. REM Sleep
    insights = run_rem_sleep_phase(cleaned)
    assert len(insights) >= 2
    # Parser errors should have high score
    parser_insights = [ins for ins in insights if ins.category == "parser"]
    assert len(parser_insights) == 1
    assert parser_insights[0].importance_score >= 0.70

    # 3. Deep Sleep
    promoted, retained = run_deep_sleep_phase(insights, threshold=0.70)
    assert len(promoted) >= 1
    assert any(p.category == "parser" for p in promoted)

    # 4. Full Cycle into store
    store = DreamStore(root_dir=tmp_path)
    report = run_dream_cycle(signals, store=store, threshold=0.70)
    assert report.promoted_count >= 1

    mem_content = store.read_memory()
    dreams_content = store.read_dreams()
    assert "PARSER" in mem_content
    assert "Dream Cycle" in dreams_content


def test_consolidate_memory_dream_tool(tmp_path: Path, monkeypatch):
    store = DreamStore(root_dir=tmp_path)
    monkeypatch.setattr("alpha.memory.dreaming.store.get_dream_store", lambda: store)
    monkeypatch.setattr("alpha.tools.builtins.dreaming_tool.get_dream_store", lambda: store)

    payload = json.dumps([
        {"content": "Always mock external HTTP APIs during unit tests", "category": "testing"},
        {"content": "Database migration failure when column already exists", "category": "db"},
        {"content": "Database timeout failure on unindexed foreign key", "category": "db"},
    ])

    res = consolidate_memory_dream.invoke({"signals_json": payload, "threshold": 0.65})
    assert "Consolidation Dream Cycle Completed" in res
    assert "Promoted" in res
    assert "MEMORY.md" in res
