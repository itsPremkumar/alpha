"""A failing temp-file cleanup must never replace the original write error.

``finally: temporary.unlink(...)`` runs while an exception is already in
flight. If the cleanup itself raises -- a temp file still held open by another
process is the realistic Windows case -- Python discards the in-flight
exception and propagates the cleanup error instead. The caller is then told
about a permission problem rather than the actual write failure, and the real
cause is lost.

``alpha/skills/proposals.py`` already guarded its cleanup. The atomic writers
below did not, and every one of them sits on a durability path (cognitive
memory, goal store, evidence store, extension config, agent config, managed
subagents, local/user skill storage, Lark flow state), so a misleading error
there is expensive to debug.

The first four were found by reading a traceback; the rest by a static sweep of
every ``finally:`` block for cleanup calls that are not wrapped in
``try/except``, ``contextlib.suppress``, or ``ignore_errors=True``.

Each test below breaks the commit step *and* the cleanup and asserts the caller
still sees the commit failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from alpha.evidence.store import EvidenceStore
from alpha.extensions.manager import _write_plugins_block
from alpha.goals.store import GoalStore
from alpha.memory.cognitive.engine import CognitiveMemorySystem

_REPLACE_ERROR = "injected replace failure"


@pytest.fixture
def cleanup_is_broken(monkeypatch):
    """Make ``Path.unlink`` fail, simulating a locked temp file.

    Only an *existing* file can be locked, so the fake raises solely when the
    target is present. That keeps the injector honest -- ``unlink(missing_ok=True)``
    on an absent path is a no-op and must not start failing -- and it stops the
    patch from interfering with unrelated ``unlink`` calls elsewhere on the
    write path (e.g. the skill projection manifest, which is deleted
    unconditionally before a write).
    """

    def _locked(self, *args, **kwargs):
        if not self.exists():
            return None
        raise PermissionError("temp file is locked by another process")

    monkeypatch.setattr(Path, "unlink", _locked)


@pytest.fixture
def replace_is_broken(monkeypatch):
    """Make the atomic commit step fail."""

    def _boom(src, dst, **kwargs):
        raise OSError(_REPLACE_ERROR)

    monkeypatch.setattr("os.replace", _boom)


def test_cognitive_memory_save_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    system = CognitiveMemorySystem(storage_dir=tmp_path)

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        system.save_to_disk()


def test_goal_store_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    store = GoalStore(tmp_path)

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        store.create_contract("ship it", owner_id="alice")


def test_evidence_store_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    store = EvidenceStore(tmp_path)

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        store.add_evidence("alice", "trace", "ref", "summary")


def test_extension_config_write_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    target = tmp_path / "config.yaml"
    target.write_text("plugins: []\n", encoding="utf-8")

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        _write_plugins_block(target, "plugins: []\n", [{"name": "x"}])


def test_cleanup_failure_alone_does_not_break_a_successful_write(tmp_path, cleanup_is_broken):
    """A locked temp file must not turn a committed write into a failure.

    ``os.replace`` has already succeeded, so the payload is durable; only the
    leftover temp file cannot be removed. That is worth swallowing -- and it is
    the same guard, exercised from the other side.
    """

    target = tmp_path / "config.yaml"
    target.write_text("plugins: []\n", encoding="utf-8")

    _write_plugins_block(target, "plugins: []\n", [{"name": "kept"}])

    assert "kept" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The same class in the other atomic writers (found by a static sweep).
# ---------------------------------------------------------------------------


def test_agent_config_write_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    from alpha.persistence.agents.file import FileAgentStore

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        FileAgentStore._write(tmp_path, {"name": "demo"}, None)


def test_managed_subagent_write_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    from alpha.persistence.managed_subagents.base import ManagedSubagentDefinition
    from alpha.persistence.managed_subagents.file import FileManagedSubagentStore

    definition = ManagedSubagentDefinition(name="demo", description="a demo", system_prompt="do it")

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        FileManagedSubagentStore._atomic_write(tmp_path / "demo.json", definition)


def test_local_skill_write_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    from alpha.skills.storage import get_or_new_skill_storage, reset_skill_storage

    reset_skill_storage()
    try:
        storage = get_or_new_skill_storage(skills_path=str(tmp_path))

        with pytest.raises(OSError, match=_REPLACE_ERROR):
            storage.write_custom_skill("demo-skill", "SKILL.md", "# hello")
    finally:
        reset_skill_storage()


def test_user_scoped_skill_write_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken):
    from unittest.mock import patch

    from alpha.config.paths import Paths
    from alpha.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage

    with patch("alpha.config.paths.get_paths", return_value=Paths(base_dir=tmp_path)):
        with patch("alpha.config.paths._paths", None):
            storage = UserScopedSkillStorage("test-user", host_path=str(tmp_path))

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        storage.write_custom_skill("demo-skill", "SKILL.md", "# hello")


def test_lark_flow_generation_reports_replace_failure(tmp_path, replace_is_broken, cleanup_is_broken, monkeypatch):
    from alpha.integrations import lark_cli

    monkeypatch.setattr(lark_cli, "ensure_lark_cli_credential_tree", lambda user_id: None)
    monkeypatch.setattr(lark_cli, "_lark_flow_state_path", lambda user_id: tmp_path / "flow.json")

    with pytest.raises(OSError, match=_REPLACE_ERROR):
        lark_cli._write_lark_flow_generation_locked("alice", "gen-1")


def test_skill_install_rollback_reports_the_install_failure(tmp_path, monkeypatch):
    """The rollback ``rmtree`` must not replace the failure that caused it.

    ``_move_staged_skill_into_reserved_target`` reserves the target directory and
    removes it again if the install did not complete. If that rollback also
    fails, an unguarded ``finally`` would report the cleanup error and hide the
    real install failure.
    """
    import shutil

    from alpha.skills import installer as installer_module

    install_error = "injected install failure"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SKILL.md").write_text("# hello", encoding="utf-8")
    target = tmp_path / "custom" / "demo-skill"
    target.parent.mkdir(parents=True)

    def _boom(*args, **kwargs):
        raise OSError(install_error)

    def _locked_rmtree(*args, **kwargs):
        raise PermissionError("target directory is locked")

    monkeypatch.setattr(installer_module, "make_skill_tree_sandbox_readable", _boom)
    monkeypatch.setattr(shutil, "rmtree", _locked_rmtree)

    with pytest.raises(OSError, match=install_error):
        installer_module._move_staged_skill_into_reserved_target(staging, target)
