"""Autonomous Agentic Variation Operators (AVO) Optimization Tool.
Implements long-horizon evolutionary search Vary(P_t) = Agent(P_t, K, f).
"""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.avo import (
    AVOEngine,
    EvaluationVector,
    VersionRecord,
)
from agent_workspace.avo.persistence import AVOPersistenceManager
from agent_workspace.avo.workspace_runner import WorkspaceAVORunner

_AVO_ENGINE = AVOEngine()
_AVO_PERSISTENCE = AVOPersistenceManager()


@tool("run_variation_operator_step", parse_docstring=True)
def run_variation_operator_step(
    action: str,
    hypothesis: str = "",
    modification: str = "",
    code_snippet: str = "",
    metrics_json: str = "{}",
    correctness: bool = True,
    query_text: str = "",
    parent_id: str | None = None,
    target_file: str = "",
    test_command: str = "",
    root_path: str | None = None,
) -> str:
    """Execute an Autonomous Agentic Variation Operator (AVO) step.

    Supports multi-dimensional vector evaluation, Pareto dominance tracking,
    domain knowledge base (K) queries, supervisory plateau intervention,
    and grounded workspace candidate execution with auto-rollback.

    Args:
        action: Action to perform: 'vary' (in-memory candidate step), 'workspace_run' (grounded file mutation with auto-rollback), 'inspect_frontier' (get Pareto solutions), 'knowledge_query' (query K), 'stats' (engine statistics), 'persist' (save to disk), 'restore' (load from disk), 'supervisor_status' (check intervention status).
        hypothesis: Testable optimization hypothesis (e.g. 'Branchless accumulator rescaling').
        modification: Description of the code / algorithmic edit.
        code_snippet: Code implementation or diff.
        metrics_json: JSON string mapping configuration names to throughput/reward metrics.
        correctness: Hard binary gate — if False, effective score is strictly zero.
        query_text: Query text for domain knowledge base lookup.
        parent_id: Optional parent version ID to branch from. Defaults to current head.
        target_file: Path to the target workspace file (required for 'workspace_run').
        test_command: Test or benchmark command (e.g. 'pytest tests/test_perf.py').
        root_path: Project root path.
    """
    try:
        metrics = json.loads(metrics_json) if metrics_json else {}
    except Exception:
        metrics = {}

    if action == "knowledge_query":
        query = query_text or hypothesis
        entries = _AVO_ENGINE.knowledge_base.query(query)
        return json.dumps({
            "query": query,
            "results": [e.to_dict() for e in entries],
        }, indent=2)

    elif action == "inspect_frontier":
        frontier = _AVO_ENGINE.lineage.get_pareto_frontier()
        return json.dumps({
            "frontier_size": len(frontier),
            "versions": [v.to_dict() for v in frontier],
        }, indent=2)

    elif action == "stats":
        return json.dumps(_AVO_ENGINE.stats(), indent=2)

    elif action == "vary":
        # Multi-dimensional vector evaluation
        vector = EvaluationVector(
            metrics=metrics,
            correctness=correctness,
            metadata={"code_snippet": code_snippet},
        )
        effective_parent = parent_id or _AVO_ENGINE.lineage.head_id

        candidate = VersionRecord(
            parent_id=effective_parent,
            hypothesis=hypothesis,
            modification=modification,
            correctness=correctness,
            vector=vector,
            performance_score=vector.geometric_mean(),
            quality_score=1.0 if correctness else 0.0,
            diff_summary=modification,
            metadata={"code": code_snippet},
        )

        committed = _AVO_ENGINE.lineage.commit_candidate(candidate)
        signature = f"{modification[:30]}_{correctness}"
        stagnated, directive, diag = _AVO_ENGINE.supervisor.observe_step(
            improved=committed,
            signature=signature,
            backtrack_candidate=effective_parent,
        )

        if committed:
            _AVO_ENGINE.knowledge_base.record_positive_pattern(
                hypothesis=hypothesis,
                modification_summary=modification,
                measured_gain=f"geomean={vector.geometric_mean()}",
            )
        else:
            _AVO_ENGINE.knowledge_base.record_negative_lesson(
                attempt_hypothesis=hypothesis,
                failure_reason=candidate.rejection_reason or "Non-improving metrics",
            )

        return json.dumps({
            "version_id": candidate.version_id,
            "committed": committed,
            "correctness": correctness,
            "geometric_mean": vector.geometric_mean(),
            "metrics": vector.metrics,
            "current_head": _AVO_ENGINE.lineage.head_id,
            "stagnation_detected": stagnated,
            "diagnostic": diag,
            "active_directive": directive.to_dict() if directive else None,
            "pareto_frontier_size": len(_AVO_ENGINE.lineage.get_pareto_frontier()),
        }, indent=2)

    elif action == "workspace_run":
        if not target_file:
            return json.dumps({
                "error": "Parameter 'target_file' is required for action 'workspace_run'."
            }, indent=2)

        runner = WorkspaceAVORunner(
            lineage=_AVO_ENGINE.lineage,
            knowledge_base=_AVO_ENGINE.knowledge_base,
            supervisor=_AVO_ENGINE.supervisor,
            persistence_mgr=_AVO_PERSISTENCE,
            root_path=root_path,
        )
        res = runner.run_workspace_variation(
            target_file_path=target_file,
            candidate_code=code_snippet,
            hypothesis=hypothesis,
            modification=modification,
            test_command=test_command if test_command else None,
            expected_metrics=metrics if metrics else None,
            parent_id=parent_id,
        )
        return json.dumps(res, indent=2)

    elif action == "persist":
        mgr = AVOPersistenceManager(base_dir=root_path) if root_path else _AVO_PERSISTENCE
        lineage_path = mgr.save_lineage(_AVO_ENGINE.lineage)
        kb_path = mgr.save_knowledge_base(_AVO_ENGINE.knowledge_base)
        return json.dumps({
            "status": "persisted",
            "lineage_path": str(lineage_path),
            "knowledge_base_path": str(kb_path),
            "versions_count": len(_AVO_ENGINE.lineage.versions),
            "knowledge_entries": len(_AVO_ENGINE.knowledge_base.entries),
        }, indent=2)

    elif action == "restore":
        mgr = AVOPersistenceManager(base_dir=root_path) if root_path else _AVO_PERSISTENCE
        loaded_lineage = mgr.load_lineage()
        loaded_kb = mgr.load_knowledge_base()
        if loaded_lineage:
            _AVO_ENGINE.lineage = loaded_lineage
        if loaded_kb:
            _AVO_ENGINE.knowledge_base = loaded_kb
        return json.dumps({
            "status": "restored",
            "lineage_restored": loaded_lineage is not None,
            "knowledge_restored": loaded_kb is not None,
            "versions_count": len(_AVO_ENGINE.lineage.versions),
            "knowledge_entries": len(_AVO_ENGINE.knowledge_base.entries),
            "head_id": _AVO_ENGINE.lineage.head_id,
        }, indent=2)

    elif action == "supervisor_status":
        sup = _AVO_ENGINE.supervisor
        return json.dumps({
            "consecutive_stagnation": sup.consecutive_stagnation,
            "max_no_improve": sup.max_no_improve,
            "recent_signatures": list(sup.signature_history),
            "last_directive": sup.last_directive.to_dict() if sup.last_directive else None,
            "total_stagnation_events": sup.total_stagnation_events,
            "total_cycle_events": sup.total_cycle_events,
        }, indent=2)

    else:
        return json.dumps({
            "error": f"Unknown action '{action}'. Supported actions: 'vary', 'workspace_run', 'inspect_frontier', 'knowledge_query', 'stats', 'persist', 'restore', 'supervisor_status'."
        }, indent=2)


# Transparent alias for backwards compatibility
run_nvidia_avo_step = run_variation_operator_step
