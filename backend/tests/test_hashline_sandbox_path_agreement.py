"""hashline tools must resolve paths through the SAME sandbox mapping as every
other filesystem tool (regression for the "file does not exist" false negative).

Before the fix, ``hashline_read``/``hashline_edit`` called
``Path(file_path).exists()`` directly, which bypasses the sandbox mapping. Two
proven lies resulted:

* a sandbox-mapped file that ``write_file`` had just created was reported as
  "does not exist", so an agent was told its own write failed;
* a host path OUTSIDE the mapping was read and edited even though ``read_file``
  and ``ls`` refused it.

These tests pin the fixed contract: one mapping, one answer, for all four tools.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from alpha.editing.hashline import compute_line_hash
from alpha.sandbox import tools as sandbox_tools
from alpha.sandbox.local.local_sandbox import LocalSandbox
from alpha.tools.builtins.hashline_tool import hashline_edit, hashline_read

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _runtime(root: Path) -> SimpleNamespace:
    """A local-sandbox runtime whose thread roots live under *root*."""
    for sub in ("workspace", "uploads", "outputs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    thread_data = {
        "workspace_path": str(root / "workspace"),
        "uploads_path": str(root / "uploads"),
        "outputs_path": str(root / "outputs"),
    }
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "local:t1"}, "thread_data": thread_data},
        context={"thread_id": "t1"},
    )


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    rt = _runtime(tmp_path / "thread")
    monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime=None: LocalSandbox("t1"))
    monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime=None: None)
    return rt


def _tag(content: str, lineno: int) -> str:
    return f"{lineno}#{compute_line_hash(content.splitlines()[lineno - 1])}"


# ---------------------------------------------------------------------------
# The false negative: all four tools must agree the file exists
# ---------------------------------------------------------------------------


def test_all_tools_agree_the_just_written_file_exists(runtime, tmp_path: Path) -> None:
    virtual = "/mnt/user-data/workspace/agreed.txt"
    content = "alpha = 1\nbeta = 2\n"

    assert sandbox_tools.write_file_tool.func(runtime=runtime, path=virtual, content=content) == "OK"
    # The bytes are really on disk under the mapped host root.
    assert (tmp_path / "thread" / "workspace" / "agreed.txt").read_text(encoding="utf-8") == content

    read_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=virtual)
    assert read_out == content, "read_file must see the file it just wrote"

    ls_out = sandbox_tools.ls_tool.func(runtime=runtime, path="/mnt/user-data/workspace")
    assert "agreed.txt" in ls_out, "ls must list the file it just wrote"

    hashline_out = hashline_read.func(runtime=runtime, file_path=virtual)
    assert "does not exist" not in hashline_out, (
        f"hashline_read contradicted write_file/read_file/ls about an existing file: {hashline_out!r}"
    )
    assert "1#" in hashline_out and "alpha = 1" in hashline_out

    edit_out = hashline_edit.func(
        runtime=runtime,
        file_path=virtual,
        start_ref=_tag(content, 2),
        end_ref=_tag(content, 2),
        replacement="beta = 200",
    )
    assert edit_out.startswith("Successfully updated"), edit_out
    assert "beta = 200" in sandbox_tools.read_file_tool.func(runtime=runtime, path=virtual)


# ---------------------------------------------------------------------------
# A path OUTSIDE the mapping: refused identically, never "missing"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["read", "edit"])
def test_outside_mapping_is_refused_by_every_tool(runtime, tmp_path: Path, action: str) -> None:
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    outside_str = str(outside)

    read_file_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=outside_str)
    ls_out = sandbox_tools.ls_tool.func(runtime=runtime, path=outside_str)
    assert "Permission denied" in read_file_out, read_file_out
    assert "Permission denied" in ls_out, ls_out

    if action == "read":
        hashline_out = hashline_read.func(runtime=runtime, file_path=outside_str)
    else:
        hashline_out = hashline_edit.func(
            runtime=runtime,
            file_path=outside_str,
            start_ref="1#00",
            end_ref="1#00",
            replacement="pwned",
        )

    # Refused, exactly like the other tools...
    assert "Permission denied" in hashline_out, hashline_out
    # ...and NOT reported as missing, which is the other half of the lie.
    assert "does not exist" not in hashline_out, hashline_out
    # The file was neither read nor modified.
    assert outside.read_text(encoding="utf-8") == "SECRET\n"
    assert "SECRET" not in hashline_out


def test_unmapped_system_path_refused_by_every_tool(runtime) -> None:
    for path in ("/etc/hosts", "/etc/passwd", "C:/Windows/win.ini"):
        read_file_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=path)
        hashline_out = hashline_read.func(runtime=runtime, file_path=path)
        assert "Permission denied" in read_file_out, (path, read_file_out)
        assert "Permission denied" in hashline_out, (path, hashline_out)
        assert "does not exist" not in hashline_out, (path, hashline_out)


# ---------------------------------------------------------------------------
# A genuinely missing file: every tool says missing (not forbidden)
# ---------------------------------------------------------------------------


def test_genuinely_missing_file_is_missing_for_every_tool(runtime) -> None:
    virtual = "/mnt/user-data/workspace/never_existed.txt"
    read_file_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=virtual)
    hashline_out = hashline_read.func(runtime=runtime, file_path=virtual)
    assert "not found" in read_file_out, read_file_out
    assert "does not exist" in hashline_out, hashline_out
    assert "Permission denied" not in hashline_out, hashline_out


# ---------------------------------------------------------------------------
# Path traversal and symlink escape
# ---------------------------------------------------------------------------


def test_dotdot_traversal_refused_by_every_tool(runtime, tmp_path: Path) -> None:
    traversal = "/mnt/user-data/workspace/../../etc/passwd"
    read_file_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=traversal)
    hashline_out = hashline_read.func(runtime=runtime, file_path=traversal)
    assert "Access denied" in read_file_out or "Permission denied" in read_file_out, read_file_out
    assert "Access denied" in hashline_out or "Permission denied" in hashline_out, hashline_out
    assert "does not exist" not in hashline_out, hashline_out


def test_symlink_escape_refused_by_every_tool(runtime, tmp_path: Path) -> None:
    secret = tmp_path / "symlink_target_secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    link = tmp_path / "thread" / "workspace" / "escape.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform without symlink rights
        pytest.skip(f"cannot create symlink on this host: {exc}")

    virtual = "/mnt/user-data/workspace/escape.txt"
    read_file_out = sandbox_tools.read_file_tool.func(runtime=runtime, path=virtual)
    hashline_out = hashline_read.func(runtime=runtime, file_path=virtual)
    assert "Access denied" in read_file_out or "Permission denied" in read_file_out, read_file_out
    assert "Access denied" in hashline_out or "Permission denied" in hashline_out, hashline_out
    assert "TOP SECRET" not in hashline_out
    assert "does not exist" not in hashline_out, hashline_out


# ---------------------------------------------------------------------------
# The shared resolver really is the single source of truth
# ---------------------------------------------------------------------------


def test_read_only_gate_rejects_writes_to_a_read_only_family(runtime) -> None:
    # /mnt/skills is read-only: a read is allowed, a write is refused. The
    # hashline tools must use the same gate as write_file/str_replace.
    from alpha.sandbox import tools as st

    skills = f"{st._get_skills_container_path()}/public"
    read_ok = st.resolve_sandbox_tool_path(runtime, f"{skills}/SKILL.md", read_only=True)
    assert read_ok == f"{skills}/SKILL.md"
    with pytest.raises(PermissionError):
        st.resolve_sandbox_tool_path(runtime, f"{skills}/SKILL.md", read_only=False)
