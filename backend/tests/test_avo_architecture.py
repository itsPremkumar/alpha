"""Comprehensive tests for NVIDIA AVO Architecture:
1. AVOPersistenceManager: Durable serialization/deserialization of P_t and K.
2. WorkspaceAVORunner: Grounded workspace file mutation with safety micro-checkpoints and auto-rollback on test failure.
3. AVOSupervisor: Stagnation and A-B-A-B oscillation detection with StrategicPivotDirective.
4. ProblemModelCompiler: 12-factor task structuring and verification planning.
5. LangChain tool integrations: run_nvidia_avo_step and compile_problem_model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alpha.avo import (
    AVOEngine,
    AVOLineage,
    AVOSupervisor,
    DomainKnowledgeBase,
    EvaluationVector,
    StrategicPivotDirective,
    VersionRecord,
)
from alpha.avo.persistence import AVOPersistenceManager
from alpha.avo.workspace_runner import WorkspaceAVORunner
from alpha.orchestration.problem_model import ProblemModelCompiler
from alpha.tools.builtins.variation_operator_tool import run_variation_operator_step
from alpha.tools.builtins.problem_model_tool import compile_problem_model


def test_avo_persistence_roundtrip(tmp_path: Path):
    """Verify durable disk persistence of lineage trees and knowledge base."""
    mgr = AVOPersistenceManager(base_dir=tmp_path)

    # 1. Populate Lineage
    lineage = AVOLineage()
    v1 = VersionRecord(
        parent_id=lineage.head_id,
        hypothesis="Vectorized inner product",
        modification="Replace loop with dot product",
        correctness=True,
        vector=EvaluationVector(metrics={"throughput": 120.0, "latency": 10.0}, correctness=True),
        performance_score=120.0,
        quality_score=1.0,
    )
    assert lineage.commit_candidate(v1) is True

    v2 = VersionRecord(
        parent_id=lineage.head_id,
        hypothesis="Cache blocking for L2",
        modification="Block size 64x64",
        correctness=True,
        vector=EvaluationVector(metrics={"throughput": 150.0, "latency": 12.0}, correctness=True),
        performance_score=150.0,
        quality_score=1.0,
    )
    assert lineage.commit_candidate(v2) is True

    # 2. Save Lineage
    lineage_path = mgr.save_lineage(lineage)
    assert lineage_path.exists()

    # 3. Load Lineage
    restored_lineage = mgr.load_lineage()
    assert restored_lineage is not None
    assert restored_lineage.head_id == v2.version_id
    assert len(restored_lineage.versions) == 2  # v1 + v2
    assert v1.version_id in restored_lineage.versions
    assert v2.version_id in restored_lineage.versions

    restored_v2 = restored_lineage.versions[v2.version_id]
    assert restored_v2.vector is not None
    assert restored_v2.vector.metrics["throughput"] == 150.0

    # 4. Save & Load Knowledge Base
    kb = DomainKnowledgeBase()
    kb.record_positive_pattern("Loop unrolling", "Unroll factor 4", "15% throughput gain")
    kb.record_negative_lesson("Naive recursion", "Exceeded stack depth and caused regression")

    kb_path = mgr.save_knowledge_base(kb)
    assert kb_path.exists()

    restored_kb = mgr.load_knowledge_base()
    assert restored_kb is not None
    assert len(restored_kb.entries) == len(kb.entries)
    query_res = restored_kb.query("unroll")
    assert len(query_res) == 1
    assert query_res[0].category == "pattern"


def test_workspace_avo_runner_success_and_rollback(tmp_path: Path):
    """Test grounded workspace candidate execution: commit on test pass vs auto-rollback on failure."""
    test_file = tmp_path / "calc.py"
    initial_code = "def compute(x):\n    return x + 1\n"
    test_file.write_text(initial_code, encoding="utf-8")

    runner = WorkspaceAVORunner(root_path=tmp_path)

    # 1. Successful candidate: modifies function to return x * 2, test verifies compute(5) == 10
    python_cmd = f'"{sys.executable}"'
    good_code = "def compute(x):\n    return x * 2\n"
    pass_test_cmd = f'{python_cmd} -c "import calc; assert calc.compute(5) == 10"'

    res_good = runner.run_workspace_variation(
        target_file_path="calc.py",
        candidate_code=good_code,
        hypothesis="Multiply by 2 for doubled throughput",
        modification="Change return x + 1 to x * 2",
        test_command=pass_test_cmd,
    )

    assert res_good["success"] is True
    assert res_good["committed"] is True
    assert res_good["rolled_back"] is False
    assert res_good["production_deployed"] is False
    assert test_file.read_text(encoding="utf-8") == good_code

    # 2. Failing candidate: syntax error or assertion failure in test
    bad_code = "def compute(x):\n    return x - 999\n"
    res_bad = runner.run_workspace_variation(
        target_file_path="calc.py",
        candidate_code=bad_code,
        hypothesis="Subtract 999",
        modification="Corrupt compute function",
        test_command=pass_test_cmd,  # compute(5) == 10 will fail!
    )

    assert res_bad["success"] is False
    assert res_bad["committed"] is False
    assert res_bad["rolled_back"] is True
    assert res_bad["workspace_state"] == "baseline_restored"
    # Auto-rollback should have restored good_code!
    assert test_file.read_text(encoding="utf-8") == good_code


def test_avo_supervisor_oscillation_break():
    """Verify that A-B-A-B cycling triggers an OSCILLATION_BREAK directive."""
    supervisor = AVOSupervisor(max_no_improve=3)

    # Alternate between two signatures without improvements
    sig_a = "signature_alpha"
    sig_b = "signature_beta"

    supervisor.observe_step(improved=False, signature=sig_a)
    supervisor.observe_step(improved=False, signature=sig_b)
    supervisor.observe_step(improved=False, signature=sig_a)
    stagnated, directive, diag = supervisor.observe_step(improved=False, signature=sig_b)

    assert stagnated is True
    assert directive is not None
    assert directive.directive_type == "OSCILLATION_BREAK"
    assert "oscillation" in diag.lower()


def test_problem_model_compiler_12_factor():
    """Verify compilation of goals into structured 12-factor ProblemModels."""
    goal = "Optimize backend fast query throughput in session.py and eliminate SQLite lock contention"
    model = ProblemModelCompiler.compile(goal)

    assert model.task_type in ("optimization", "coding")
    assert model.domain == "backend"
    assert any("session.py" in e for e in model.entities)
    assert len(model.constraints) >= 2
    assert len(model.verification_methods) >= 1
    assert "run_nvidia_avo_step" in model.required_tools or "auto_test_and_repair" in model.required_tools

    # Markdown format check
    md = model.to_markdown()
    assert "# AVO Problem Model" in md
    assert "Constraints & Assumptions" in md
    assert "Success Metrics" in md

    # JSON dict check
    d = model.to_dict()
    assert d["objective"] == goal
    assert d["domain"] == "backend"


def test_nvidia_avo_tool_actions(tmp_path: Path):
    """Test all tool actions provided by run_variation_operator_step and compile_problem_model."""
    # 1. Compile problem model tool
    pm_md = compile_problem_model.invoke({"goal": "Refactor auth tokens in backend/auth.py", "as_markdown": True})
    assert "# AVO Problem Model" in pm_md
    assert "auth.py" in pm_md

    pm_json = compile_problem_model.invoke({"goal": "Refactor auth tokens in backend/auth.py", "as_markdown": False})
    data = json.loads(pm_json)
    assert data["domain"] in ("backend", "security")

    # 2. Stats
    stats_out = run_variation_operator_step.invoke({"action": "stats"})
    stats = json.loads(stats_out)
    assert "lineage" in stats
    assert "head_id" in stats["lineage"]

    # 3. Vary (in-memory candidate)
    vary_out = run_variation_operator_step.invoke(
        {
            "action": "vary",
            "hypothesis": "SIMD register caching",
            "modification": "AVX-512 register unroll",
            "metrics_json": json.dumps({"throughput": 250.0}),
            "correctness": True,
        }
    )
    vary_res = json.loads(vary_out)
    assert vary_res["committed"] is True
    assert vary_res["correctness"] is True

    # 4. Frontier Inspection
    frontier_out = run_variation_operator_step.invoke({"action": "inspect_frontier"})
    frontier = json.loads(frontier_out)
    assert frontier["frontier_size"] >= 1

    # 5. Knowledge Query
    kq_out = run_variation_operator_step.invoke(
        {
            "action": "knowledge_query",
            "query_text": "register",
        }
    )
    kq = json.loads(kq_out)
    assert "results" in kq

    # 6. Persist & Restore
    persist_out = run_variation_operator_step.invoke(
        {
            "action": "persist",
            "root_path": str(tmp_path),
        }
    )
    p_data = json.loads(persist_out)
    assert p_data["status"] == "persisted"
    assert (tmp_path / ".avo" / "lineage.json").exists()

    restore_out = run_variation_operator_step.invoke(
        {
            "action": "restore",
            "root_path": str(tmp_path),
        }
    )
    r_data = json.loads(restore_out)
    assert r_data["status"] == "restored"
    assert r_data["lineage_restored"] is True

    # 7. Supervisor Status
    step_res = run_variation_operator_step.invoke({"action": "supervisor_status"})
    sup = json.loads(step_res)
    assert "consecutive_stagnation" in sup
    assert "max_no_improve" in sup
