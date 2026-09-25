"""Validation tests for the opt-in source-update policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.evolution.update_policy import UpdatePolicyError, load_update_policy


def _write(path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "schema_version": 1,
        "enabled": True,
        "auto_apply": True,
        "channel": "main",
        "remote": "origin",
        "branch": "main",
        "health_urls": ["http://127.0.0.1:8001/health/ready"],
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_policy_file_can_enable_main_with_explicit_opt_ins(tmp_path: Path) -> None:
    policy = load_update_policy(_write(tmp_path / "policy.json"))

    assert policy.enabled is True
    assert policy.auto_apply is True
    assert policy.channel == "main"
    assert policy.require_clean_worktree is True
    assert policy.rollback_on_failure is True


def test_auto_apply_cannot_silently_enable_a_disabled_policy(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", enabled=False)
    with pytest.raises(UpdatePolicyError, match="requires enabled=true"):
        load_update_policy(path)


def test_policy_rejects_non_loopback_health_probe(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", health_urls=["https://example.com/health"])
    with pytest.raises(UpdatePolicyError, match="loopback"):
        load_update_policy(path)


def test_policy_rejects_unknown_fields_instead_of_typo_silently_disabling_safety(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", require_clean_wroktree=True)
    with pytest.raises(UpdatePolicyError, match="unknown update policy field"):
        load_update_policy(path)


def test_policy_rejects_dangerous_branch_shape(tmp_path: Path) -> None:
    path = _write(tmp_path / "policy.json", branch="main; rm -rf /")
    with pytest.raises(UpdatePolicyError, match="unsafe"):
        load_update_policy(path)
