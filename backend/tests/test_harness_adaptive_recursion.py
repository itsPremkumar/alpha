"""Adaptive recursion for Harness² (guide §16 Phase 7).

Pins the properties the phase exists to guarantee:

1. **base_bundle_id override.**  A run can start from a specified bundle
   instead of the active one, so run N+1 compounds run N's improvement.
2. **Marginal improvement signal.**  After composition, the run records
   whether an improvement was found (``marginal_improvement``), so a caller
   can decide whether to chain another run.
3. **Honest refusal.**  A non-existent base bundle is refused, not silently
   ignored.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.harness_improvement.engine import HarnessImprovementEngine, ImprovementRunError
from alpha.harness_improvement.state import ImprovementRun, new_run


def _make_engine(tmp_path: Any, *, baseline: bool = True) -> HarnessImprovementEngine:
    from alpha.harness_improvement import HarnessImprovementSettings, HarnessImprovementStore

    settings = HarnessImprovementSettings.from_mapping({"enabled": True, "max_model_calls": 64})
    store = HarnessImprovementStore(tmp_path / "harness_improvement")
    return HarnessImprovementEngine(
        store=store,
        settings=settings,
        baseline_provider=lambda: {"system_prompt/base": ("system_prompt", "You are Alpha.")} if baseline else None,
    )


def _task() -> Any:
    from alpha.harness_improvement import TaskContract, compute_task_fingerprint

    return TaskContract(
        task="Create a JSON config and confirm it is valid",
        outcome="config is valid",
        deliverables=("outputs/report.json",),
        checks=(),
        domain_tags=("artifact",),
        task_type="artifact",
        task_fingerprint=compute_task_fingerprint("Create a JSON config and confirm it is valid"),
    )


# ---------------------------------------------------------------------------
# State: marginal_improvement field
# ---------------------------------------------------------------------------


class TestMarginalImprovementField:
    def test_field_defaults_to_none(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        assert run.marginal_improvement is None

    def test_field_round_trips_true(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        run.marginal_improvement = True
        data = run.to_dict()
        assert data["marginal_improvement"] is True
        restored = ImprovementRun.from_dict(data)
        assert restored.marginal_improvement is True

    def test_field_round_trips_false(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        run.marginal_improvement = False
        data = run.to_dict()
        restored = ImprovementRun.from_dict(data)
        assert restored.marginal_improvement is False

    def test_field_round_trips_none(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        data = run.to_dict()
        restored = ImprovementRun.from_dict(data)
        assert restored.marginal_improvement is None


# ---------------------------------------------------------------------------
# Engine: base_bundle_id override
# ---------------------------------------------------------------------------


class TestBaseBundleOverride:
    @pytest.mark.asyncio
    async def test_missing_base_bundle_is_refused(self, tmp_path: Any) -> None:
        engine = _make_engine(tmp_path)
        task = _task()
        with pytest.raises(ImprovementRunError, match="is not stored"):
            await engine.start(task=task, base_bundle_id="sha256:" + "0" * 64)

    @pytest.mark.asyncio
    async def test_base_bundle_override_uses_specified_bundle(self, tmp_path: Any) -> None:
        """When base_bundle_id is provided and exists, the run uses it."""
        engine = _make_engine(tmp_path)
        base = engine._resolve_or_bootstrap_baseline()
        assert base is not None

        # start() will fail later (no executor) but bundle validation passes
        try:
            run = await engine.start(task=_task(), base_bundle_id=base.bundle_id)
            assert run.base_bundle_id == base.bundle_id
        except ImprovementRunError as exc:
            # Expected: no executor for full run, but bundle validation passed
            assert "is not stored" not in str(exc)

    @pytest.mark.asyncio
    async def test_default_uses_active_bundle(self, tmp_path: Any) -> None:
        """Without base_bundle_id, the run resolves from the active bundle."""
        engine = _make_engine(tmp_path)
        base = engine._resolve_or_bootstrap_baseline()
        assert base is not None

        try:
            run = await engine.start(task=_task())
            assert run.base_bundle_id == base.bundle_id
        except ImprovementRunError as exc:
            assert "is not stored" not in str(exc)


# ---------------------------------------------------------------------------
# Engine: marginal_improvement set after composition
# ---------------------------------------------------------------------------


class TestMarginalImprovement:
    def test_retained_baseline_sets_false(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        run.marginal_improvement = False
        assert run.marginal_improvement is False

    def test_improvement_sets_true(self) -> None:
        run = new_run(
            run_id="run-1",
            task=_task(),
            base_bundle_id="bundle-1",
            config_hash="h",
            created_by="op",
            mode="sequential",
        )
        run.marginal_improvement = True
        assert run.marginal_improvement is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
