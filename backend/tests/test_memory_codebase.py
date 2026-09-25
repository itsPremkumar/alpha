"""Hermetic regression tests for codebase structure memory."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from alpha.memory.codebase.config import CodebaseConfig
from alpha.memory.codebase.graph import CycleError, DependencyGraph
from alpha.memory.codebase.hotspots import score_hotspots
from alpha.memory.codebase.impact import compute_change_impact, is_test_path
from alpha.memory.codebase.indexer import CodebaseIndexer, FileEntry
from alpha.memory.codebase.models import (
    ChangeImpact,
    CodebaseSnapshot,
    DependencyEdge,
    ModuleRecord,
    SymbolRef,
)
from alpha.memory.codebase.paths import (
    atomic_write_text,
    codebase_root,
    l1_root,
    repo_dir,
    safe_segment,
    snapshot_path,
)
from alpha.memory.codebase.recall import (
    CodebaseRecaller,
    impact_block,
    module_summary_block,
    structure_block,
    symbols_block,
)
from alpha.memory.codebase.refresh import CodebaseRefresh
from alpha.memory.codebase.store import CodebaseStore


def _entry(path: str, content: str) -> FileEntry:
    encoded = content.encode("utf-8")
    return FileEntry(path=path, content_hash=hashlib.sha256(encoded).hexdigest(), size_bytes=len(encoded), content=None)


def _config(tmp_path: Path, **overrides: object) -> CodebaseConfig:
    values: dict[str, object] = {"enabled": True, "index_extensions": (".py", ".js"), "storage_path": str(tmp_path / "state")}
    values.update(overrides)
    return CodebaseConfig(**values)


def _snapshot(paths: list[str], *, partial: bool = False) -> CodebaseSnapshot:
    modules = [
        ModuleRecord(
            path=path,
            summary=f"responsibility for {path}",
            symbols=[SymbolRef(path=path, qualified_name=path.replace("/", "."), kind="module", line_start=1, line_end=2)],
            imports=[],
            language="python",
            content_hash=path,
            indexed_at=1.0,
        )
        for path in paths
    ]
    return CodebaseSnapshot(repo_id="repo", modules=modules, edges=[], coverage=1.0, partial_index=partial, disclosures=["partial"] if partial else [])


def test_default_off_never_lists_or_reads(tmp_path: Path) -> None:
    listed = False
    read = False

    def lister() -> list[str]:
        nonlocal listed
        listed = True
        return ["secret.py"]

    def reader(path: str) -> str:
        nonlocal read
        read = True
        return "x = 1"

    indexer = CodebaseIndexer(CodebaseConfig(storage_path=str(tmp_path)), lister, reader)
    snapshot = indexer.index()
    assert snapshot.modules == []
    assert snapshot.disclosures == ["disabled"]
    assert not listed
    assert not read
    assert CodebaseRecaller(CodebaseConfig(), snapshot).symbols("secret") == ""


def test_incremental_hash_reindex_reads_only_changed_file(tmp_path: Path) -> None:
    contents = {
        "a.py": '"""A."""\nA = 1\n',
        "b.py": '"""B."""\nB = 2\n',
    }
    reads: list[str] = []

    def lister() -> list[FileEntry]:
        return [_entry(path, contents[path]) for path in sorted(contents)]

    def reader(path: str) -> str:
        reads.append(path)
        return contents[path]

    indexer = CodebaseIndexer(_config(tmp_path), lister, reader, clock=lambda: 10.0)
    first = indexer.index()
    assert sorted(reads) == ["a.py", "b.py"]
    assert set(first.changed_paths) == {"a.py", "b.py"}
    reads.clear()
    second = indexer.index(first)
    assert reads == []
    assert second.changed_paths == []
    assert second.read_paths == []
    contents["b.py"] = '"""B changed."""\nB = 3\n'
    reads.clear()
    third = indexer.index(second)
    assert reads == ["b.py"]
    assert third.changed_paths == ["b.py"]
    assert {module.path: module.content_hash for module in third.modules}["b.py"] == hashlib.sha256(contents["b.py"].encode()).hexdigest()


def test_incremental_preserves_unchanged_edges(tmp_path: Path) -> None:
    contents = {
        "lib.py": "def helper():\n    return 1\n",
        "app.py": "from lib import helper\n\ndef run():\n    return helper()\n",
    }
    entries = [_entry(path, contents[path]) for path in sorted(contents)]
    indexer = CodebaseIndexer(_config(tmp_path), lambda: entries, lambda path: contents[path], clock=lambda: 1.0)
    first = indexer.index()
    assert any(edge.kind == "import" and edge.to_path == "lib.py" for edge in first.edges)
    second = indexer.index(first)
    assert {(edge.from_path, edge.to_path, edge.kind) for edge in second.edges} == {(edge.from_path, edge.to_path, edge.kind) for edge in first.edges}


def test_caps_are_deterministic_and_disclosed(tmp_path: Path) -> None:
    contents = {"a.py": "A = 1\n", "b.py": "B = 22222\n", "c.py": "C = 3\n"}
    indexer = CodebaseIndexer(
        _config(tmp_path, max_files=2, max_file_bytes=6),
        lambda: [_entry(path, contents[path]) for path in ("c.py", "a.py", "b.py")],
        lambda path: contents[path],
        clock=lambda: 1.0,
    )
    first = indexer.index()
    second = indexer.index()
    assert first.skipped_files == second.skipped_files == ["b.py", "c.py"]
    assert first.skipped_reasons["c.py"] == "max_files"
    assert first.skipped_reasons["b.py"] == "max_file_bytes"
    assert "max_files_cap" in " ".join(first.disclosures)
    assert "max_file_bytes" in " ".join(first.disclosures)
    assert first.partial_index is True


def test_unparsed_language_is_explicit_and_uses_regex_import_scan(tmp_path: Path) -> None:
    source = 'import { thing } from "pkg/mod";\nconst value = require("other");\n'
    indexer = CodebaseIndexer(
        _config(tmp_path, index_extensions=(".js",)),
        lambda: [FileEntry("view.js", content_hash="hash", size_bytes=len(source.encode()))],
        lambda path: source,
        clock=lambda: 1.0,
    )
    snapshot = indexer.index()
    assert snapshot.partial_index is True
    assert snapshot.unparsed_files == ["view.js"]
    assert "other" in snapshot.modules[0].imports
    assert "pkg/mod" in snapshot.modules[0].imports
    assert "unparsed" in " ".join(snapshot.disclosures)


def test_python_parser_records_symbols_without_execution(tmp_path: Path) -> None:
    source = '''"""Responsibility."""\nfrom dataclasses import dataclass\n\n@dataclass\nclass Record:\n    value: int\n\ndef build() -> Record:\n    return Record(value=1)\n'''
    marker = tmp_path / "should_not_exist"
    source += f"\n# {marker}\n"
    indexer = CodebaseIndexer(
        _config(tmp_path, index_extensions=(".py",)),
        lambda: [FileEntry("models.py", content_hash="h", size_bytes=len(source.encode()))],
        lambda path: source,
        clock=lambda: 1.0,
    )
    snapshot = indexer.index()
    record = snapshot.modules[0]
    assert record.summary == "Responsibility."
    assert {(symbol.qualified_name, symbol.kind, symbol.path) for symbol in record.symbols} >= {
        ("models.Record", "class", "models.py"),
        ("models.build", "function", "models.py"),
    }
    assert not marker.exists()


def test_duplicate_and_symlinkish_paths_are_cycle_safe(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    try:
        (root / "alias.py").symlink_to(root / "a.py")
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    indexer = CodebaseIndexer(_config(tmp_path), lambda: [root / "a.py", root / "alias.py", root / "." / "a.py"], lambda path: (root / path).read_text(encoding="utf-8"), repo_root=root, clock=lambda: 1.0)
    snapshot = indexer.index()
    assert len(snapshot.modules) == 1
    assert "duplicate_path" in " ".join(snapshot.disclosures)


def test_graph_topology_cycles_reachability_and_fan_in_ties() -> None:
    graph = DependencyGraph(
        [
            DependencyEdge(from_path="b", to_path="a", kind="import"),
            DependencyEdge(from_path="c", to_path="b", kind="import"),
            DependencyEdge(from_path="x", to_path="root", kind="import"),
            DependencyEdge(from_path="y", to_path="root", kind="import"),
            DependencyEdge(from_path="z", to_path="a", kind="import"),
        ]
    )
    order = graph.topological_order()
    assert order == graph.topological_order()
    assert order.index("c") < order.index("b") < order.index("a")
    assert order.index("x") < order.index("root")
    assert graph.reachable("a", 1, direction="in") == {"b": 1, "z": 1}
    assert graph.reachable("a", 2, direction="in")["c"] == 2
    assert graph.fan_in_ranking()[:3] == [("a", 2), ("root", 2), ("b", 1)]
    cycle = DependencyGraph([DependencyEdge(from_path="a", to_path="b", kind="import"), DependencyEdge(from_path="b", to_path="a", kind="import")])
    assert cycle.detect_cycles() == [["a", "b", "a"]]
    with pytest.raises(CycleError):
        cycle.topological_sort()
    assert cycle.reachable("a", 20) == {"b": 1}
    assert cycle.remove_edge("a", "b") is True


def test_impact_depth_tests_and_partial_disclosure() -> None:
    edges = [
        DependencyEdge(from_path="tests/test_service.py", to_path="service.py", kind="import"),
        DependencyEdge(from_path="api.py", to_path="service.py", kind="import"),
        DependencyEdge(from_path="web.py", to_path="api.py", kind="import"),
    ]
    impact = compute_change_impact(edges, "service.py", depth_cap=2)
    assert impact.directly_affected == ["api.py", "tests/test_service.py"]
    assert impact.transitively_affected == {"web.py": 2}
    assert impact.test_files == ["tests/test_service.py"]
    assert impact.confidence == "high"
    partial = compute_change_impact(_snapshot(["service.py"], partial=True), "service.py", partial_index=True)
    assert partial.partial_index is True
    assert "partial_index" in " ".join(partial.disclosures)
    assert partial.confidence == "low"
    assert is_test_path("src/foo.spec.ts")


def test_hotspots_formula_and_missing_churn_are_disclosed() -> None:
    graph = DependencyGraph([DependencyEdge(from_path="a.py", to_path="hot.py", kind="import")])
    complete = score_hotspots(["hot.py", "cold.py"], commit_timestamps={"hot.py": [0.0]}, edit_counts={"hot.py": 3}, fan_in=graph, now=0.0)
    assert complete[0].path == "hot.py"
    assert complete[0].heuristic is True
    missing = score_hotspots(["hot.py"], fan_in={"hot.py": 2}, now=0.0)
    assert missing[0].churn_score is None
    assert "churn" in missing[0].missing_inputs
    assert "missing churn" in " ".join(missing[0].disclosures)
    no_signals = score_hotspots(["hot.py"])
    assert no_signals[0].score is None
    assert set(no_signals[0].missing_inputs) == {"churn", "fan_in"}


def test_store_round_trip_versions_bounded_history_and_corruption(tmp_path: Path) -> None:
    store = CodebaseStore(tmp_path / "store", repo_id="repo", max_history=2)
    first = store.save(_snapshot(["a.py"]))
    second = store.save(_snapshot(["a.py", "b.py"]))
    third = store.save(_snapshot(["a.py", "b.py", "c.py"]))
    assert [item.version for item in (first, second, third)] == [1, 2, 3]
    assert [item.version for item in store.history()] == [2, 3]
    assert store.load().module_count == 3
    assert store.get_version(2).module_count == 2
    assert third.evicted_versions == [1]
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None
    assert list(store.repo_path.glob("snapshots.json.corrupt-*"))
    assert store.path_for("repo") == store.path
    assert store.list_history("repo") == []
    store.save(_snapshot(["a.py"]))
    store.clear("repo")
    assert store.load("repo") is None


def test_recall_budget_empty_and_deterministic_ranking(tmp_path: Path) -> None:
    modules = [
        ModuleRecord(path="z.py", summary="zebra", symbols=[SymbolRef(path="z.py", qualified_name="z", kind="function", line_start=1, line_end=1)], content_hash="z"),
        ModuleRecord(path="a.py", summary="alpha", symbols=[SymbolRef(path="a.py", qualified_name="target", kind="function", line_start=2, line_end=3)], content_hash="a"),
    ]
    snapshot = CodebaseSnapshot(repo_id="repo", modules=modules, coverage=1.0)
    assert symbols_block(snapshot, "") == ""
    assert module_summary_block(snapshot, "does-not-exist") == ""
    ranked = symbols_block(snapshot, "target", char_budget=1000)
    assert ranked == symbols_block(snapshot, "target", char_budget=1000)
    assert ranked.index("a.py") < ranked.index("z.py") if "z.py" in ranked else True
    bounded = symbols_block(snapshot, "function", char_budget=45)
    assert len(bounded) <= 45
    assert "truncated" in bounded
    impact = ChangeImpact(path="a.py", directly_affected=["b.py"], transitively_affected={"c.py": 2}, test_files=["tests/test_a.py"], confidence="medium", computation="test")
    assert "truncated" in impact_block(impact, char_budget=55)
    assert len(structure_block(snapshot, "alpha", char_budget=1000)) <= 1000


def test_refresh_reports_diff_and_concurrent_calls_publish_consistent_snapshot(tmp_path: Path) -> None:
    contents = {"a.py": "A = 1\n", "b.py": "B = 1\n"}
    barrier = threading.Barrier(2)
    reads: list[str] = []
    lock = threading.Lock()

    def lister() -> list[FileEntry]:
        return [_entry(path, contents[path]) for path in sorted(contents)]

    def reader(path: str) -> str:
        with lock:
            reads.append(path)
        return contents[path]

    config = _config(tmp_path, max_snapshot_history=5)
    indexer = CodebaseIndexer(config, lister, reader, repo_root=None, clock=lambda: 1.0)
    refresh = CodebaseRefresh(config, indexer=indexer, repo_id="repo")

    def run_one() -> object:
        barrier.wait()
        return refresh.refresh()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda _: run_one(), range(2)))
    assert all(report.status == "succeeded" for report in reports)
    latest = refresh.store.load("repo")
    assert latest is not None
    assert latest.module_count == 2
    assert latest.partial_index is False
    assert all(item.module_count == len(item.modules) for item in refresh.store.history("repo"))


def test_paths_and_all_config_keys_have_readers(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    assert safe_segment("../x/y") == "x_y"
    assert safe_segment(None) == "__default__"
    assert codebase_root(root) == root.resolve()
    assert l1_root(root) == root.resolve()
    assert repo_dir(root, "repo/a") == root.resolve() / "repos" / "repo_a"
    path = snapshot_path(root, "repo/a")
    assert path.parent.name == "repo_a"
    atomic_write_text(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"

    config = CodebaseConfig(
        enabled=True,
        max_files=2,
        max_file_bytes=100,
        max_symbols_per_file=2,
        max_snapshot_history=1,
        symbols_char_budget=100,
        module_summary_char_budget=100,
        impact_char_budget=100,
        structure_char_budget=100,
        max_recall_items=1,
        max_impact_depth=1,
        index_extensions=[".py"],
        storage_path=str(root),
    )
    source = "def f():\n    return 1\n"
    indexer = CodebaseIndexer(config, lambda: [FileEntry("x.py", content_hash="h", size_bytes=len(source))], lambda path: source, clock=lambda: 1.0)
    snapshot = indexer.index()
    store = CodebaseStore(config=config)
    store.save(snapshot)
    coordinator = CodebaseRefresh(config, store=store, repo_id="config-reader")
    assert coordinator.change_impact(snapshot, "x.py").depth_cap == 1
    recaller = CodebaseRecaller(config, snapshot)
    assert len(recaller.symbols("f", char_budget=10)) <= 10  # explicit tiny budget is honored
    assert recaller.module_summary("x", char_budget=100)
    assert recaller.structure("x", char_budget=100)
    impact = ChangeImpact(path="changed.py", directly_affected=["x.py"], confidence="high", computation="config-reader-test")
    assert recaller.impact(impact, char_budget=100)
    assert compute_change_impact(snapshot, "x.py", depth_cap=config.max_impact_depth).depth_cap == 1
    assert config.recall_budgets == {"symbols": 100, "module_summary": 100, "impact": 100, "structure": 100}


def test_snapshot_json_shape_is_portable(tmp_path: Path) -> None:
    store = CodebaseStore(tmp_path, repo_id="repo")
    snapshot = _snapshot(["a.py"])
    store.save(snapshot)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["snapshots"][0]["modules"][0]["path"] == "a.py"


def test_change_impact_empty_graph_stays_conservative() -> None:
    impact = compute_change_impact([], "missing.py")
    assert impact.directly_affected == []
    assert impact.transitively_affected == {}
    assert impact.confidence == "low"
    assert impact.disclosures


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
