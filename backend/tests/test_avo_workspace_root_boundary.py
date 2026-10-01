"""The AVO runner's root must be bounded before it is treated as server-owned.

`WorkspaceAVORunner` is allowed to declare its own root authoritative
(`root_is_trusted=True`) because it owns the root and enforces containment over
it. That authority is only sound if the root was bounded first.

`run_variation_operator_step` is model-facing and passes `root_path` straight
into the runner, and the runner's own containment check is *relative to whatever
root it is given*. So an unbounded model-supplied root would let the model choose
its own boundary and then mutate anything inside it. The bound therefore belongs
at the model-facing edge; these tests pin that it is actually there.
"""

from __future__ import annotations

import json

from alpha.tools.builtins.variation_operator_tool import run_variation_operator_step


def test_model_cannot_name_a_workspace_root_outside_the_boundary(monkeypatch, tmp_path):
    outside = tmp_path / "not-the-project"
    outside.mkdir()
    victim = outside / "calc.py"
    victim.write_text("ORIGINAL = 1\n", encoding="utf-8")
    inside = tmp_path / "project"
    inside.mkdir()
    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(inside))

    out = run_variation_operator_step.invoke(
        {
            "action": "workspace_run",
            "root_path": str(outside),
            "target_file": "calc.py",
            "code_snippet": "ORIGINAL = 2",
            "hypothesis": "h",
            "modification": "m",
        }
    )
    payload = json.loads(out)
    assert payload.get("success") is False
    assert "workspace root" in payload.get("error", "")
    # And the file outside the boundary is untouched.
    assert victim.read_text(encoding="utf-8") == "ORIGINAL = 1\n"


def test_parent_traversal_as_a_root_is_refused(monkeypatch, tmp_path):
    inside = tmp_path / "project"
    inside.mkdir()
    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(inside))
    out = run_variation_operator_step.invoke(
        {
            "action": "workspace_run",
            "root_path": str(inside / ".." / ".." / ".."),
            "target_file": "x.py",
            "code_snippet": "pass",
            "hypothesis": "h",
            "modification": "m",
        }
    )
    assert json.loads(out).get("success") is False


def test_a_declared_workspace_root_is_accepted(monkeypatch, tmp_path):
    """The gate must not block a legitimately declared out-of-project workspace.

    This is the feature-preservation half: an operator who declares a workspace
    gets it, so the fix is a bound rather than a blanket refusal.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(tmp_path / "project"))
    (tmp_path / "project").mkdir()
    monkeypatch.setenv("ALPHA_WORKSPACE_ROOTS", str(root))

    from alpha.sandbox.workspace_boundary import resolve_workspace_root

    assert resolve_workspace_root(str(root)) == root.resolve()
