"""Retrospective <-> postmortem wiring pins (root-cause fix).

The engine previously imported a nonexistent ``get_postmortem_store`` and
swallowed every failure with ``except Exception: pass``, so heuristic
synthesis could never observe a real postmortem — the generic fallback
proposal always fired regardless of history. These pins wire
``analyze_recent_learnings`` to the landed ``alpha.projects.postmortem``
API:

- a REAL recorded postmortem yields the import-failure proposal carrying
  the real ``error_summary`` text (not the generic fallback);
- a postmortem load failure is disclosed at WARNING with the real error
  text (never silently swallowed);
- an approval-queue failure is disclosed at WARNING with the real error
  text while synthesis still returns honestly.

Deterministic: ``AGENT_WORKSPACE_HOME`` -> ``tmp_path``, unique project ids
(per-test engine caches), no network, no subprocesses.
"""

from __future__ import annotations

import logging
import uuid


def test_real_postmortem_yields_proposal_with_real_error_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    from alpha.evolution.retrospective_engine import RetrospectiveEngine
    from alpha.projects.postmortem import get_postmortem_engine

    project = f"retro-{uuid.uuid4().hex[:8]}"
    record = get_postmortem_engine(project).analyze_failure(
        task_id="task-1",
        bot_name="coder",
        error_summary="ModuleNotFoundError: No module named 'alpha.missing_mod'",
        root_cause="import of an undeclared module",
        sync_to_memory=False,
    )
    assert record.error_summary.startswith("ModuleNotFoundError")  # a real record exists

    proposals = RetrospectiveEngine(project).analyze_recent_learnings(queue_for_approval=False)
    patterns = [p.trigger_pattern for p in proposals]
    synth = [p for p in proposals if p.trigger_pattern == "Recurring Import / Module Resolution Failure"]
    assert len(synth) == 1, f"expected exactly the import-failure synthesis from the real postmortem, got {patterns}"
    assert "ModuleNotFoundError: No module named 'alpha.missing_mod'" in synth[0].heuristic_summary
    assert "Proactive Quality Hardening" not in patterns  # synthesis found evidence — no generic fallback


def test_postmortem_load_failure_logged_with_real_text(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    from alpha.evolution.retrospective_engine import RetrospectiveEngine

    def _boom(_project_id):
        raise RuntimeError("postmortem store unavailable")

    monkeypatch.setattr("alpha.projects.postmortem.get_postmortem_engine", _boom)
    with caplog.at_level(logging.WARNING, logger="alpha.evolution.retrospective_engine"):
        proposals = RetrospectiveEngine(f"retro-{uuid.uuid4().hex[:8]}").analyze_recent_learnings(
            queue_for_approval=False
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "could not load postmortems" in w and "RuntimeError: postmortem store unavailable" in w
        for w in warnings
    ), f"real load failure not disclosed at WARNING: {warnings}"
    # honest disclosure of state: with no evidence, only the generic fallback exists
    assert [p.trigger_pattern for p in proposals] == ["Proactive Quality Hardening"]


def test_approval_queue_failure_logged_with_real_text(caplog, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    from alpha.evolution.retrospective_engine import RetrospectiveEngine

    def _queue_boom(_project_id):
        raise RuntimeError("approval queue down")

    monkeypatch.setattr("alpha.projects.approval_queue.get_approval_queue", _queue_boom)
    with caplog.at_level(logging.WARNING, logger="alpha.evolution.retrospective_engine"):
        proposals = RetrospectiveEngine(f"retro-{uuid.uuid4().hex[:8]}").analyze_recent_learnings(
            queue_for_approval=True
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(proposals) == 1  # synthesis still returns honestly
    assert any(
        "could not queue prompt evolution proposal" in w
        and proposals[0].proposal_id in w
        and "RuntimeError: approval queue down" in w
        for w in warnings
    ), f"queue failure not disclosed at WARNING with proposal id: {warnings}"
