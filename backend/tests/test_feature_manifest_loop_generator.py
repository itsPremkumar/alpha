"""Regression coverage for the feature manifest's supervisor-loop extraction."""

from __future__ import annotations

import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_loop_generator_reads_only_the_default_registry() -> None:
    """Unrelated tuples in supervisor code must not become capability rows."""
    path = BACKEND / "scripts" / "generate_feature_manifest.py"
    spec = importlib.util.spec_from_file_location("_feature_manifest_generator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ids = {entry["id"] for entry in module.collect_loops()}
    assert "error" not in ids
    assert ids == {
        "sentinel",
        "perpetual",
        "review_queue",
        "skill_curator",
        "enterprise_heartbeat",
        "swarm_status",
        "free_models_sync",
        "company_operations",
        "self_update",
        "apex",
    }
