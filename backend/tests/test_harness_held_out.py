"""Held-out transfer measurement (guide §20 Definition of Done).

The first release is ready when baseline-versus-improved results are
measured on a fixed synthetic suite AND a held-out task set.  These tests
pin that:

1. The synthetic and held-out suites are non-empty and distinct.
2. Every synthetic and held-out task converts to a valid TaskContract.
3. The held-out tasks exercise the same shape with different names and
   tags, so a lesson that only generalises to training names cannot pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.harness_improvement import TaskContract, compute_task_fingerprint
from alpha.harness_improvement.metrics import HELD_OUT_TASKS, SYNTHETIC_TASKS, held_out_suite, synthetic_suite


def _task_from_synthetic(st: Any) -> TaskContract:
    from alpha.harness_improvement import DeliverableCheck

    return TaskContract(
        task=st.name,
        outcome=st.outcome,
        deliverables=st.deliverables,
        checks=tuple(DeliverableCheck(name=c.name, path=c.path, kind=c.kind, sections=list(c.sections), min_bytes=c.min_bytes) for c in st.checks),
        domain_tags=st.domain_tags,
        task_type=st.task_type,
        task_fingerprint=compute_task_fingerprint(st.name),
    )


# ---------------------------------------------------------------------------
# Suite shape
# ---------------------------------------------------------------------------


class TestSuiteShape:
    def test_synthetic_suite_is_non_empty(self) -> None:
        suite = synthetic_suite()
        assert len(suite) > 0

    def test_held_out_suite_is_non_empty(self) -> None:
        suite = held_out_suite()
        assert len(suite) > 0

    def test_suites_are_distinct_by_name(self) -> None:
        training = {t.name for t in SYNTHETIC_TASKS}
        held_out = {t.name for t in HELD_OUT_TASKS}
        assert training.isdisjoint(held_out)

    def test_held_out_shares_task_types(self) -> None:
        training_types = {t.task_type for t in SYNTHETIC_TASKS}
        held_out_types = {t.task_type for t in HELD_OUT_TASKS}
        assert held_out_types.issubset(training_types)

    def test_held_out_has_distinct_tags(self) -> None:
        held_out_tags = {tag for t in HELD_OUT_TASKS for tag in t.domain_tags}
        assert "held-out" in held_out_tags


# ---------------------------------------------------------------------------
# Task conversion
# ---------------------------------------------------------------------------


class TestTaskConversion:
    def test_synthetic_tasks_convert_to_task_contracts(self) -> None:
        for st in synthetic_suite():
            task = _task_from_synthetic(st)
            assert task.task == st.name
            assert task.task_type == st.task_type
            assert task.task_fingerprint != ""

    def test_held_out_tasks_convert_to_task_contracts(self) -> None:
        for st in held_out_suite():
            task = _task_from_synthetic(st)
            assert task.task == st.name
            assert task.task_type == st.task_type
            assert task.task_fingerprint != ""

    def test_held_out_tasks_have_distinct_names(self) -> None:
        training_names = {t.name for t in SYNTHETIC_TASKS}
        for st in HELD_OUT_TASKS:
            assert st.name not in training_names


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
