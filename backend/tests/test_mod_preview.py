"""Unit tests for the deterministic impact preview."""

from alpha.mods.preview import ImpactKind, preview_impact


class TestDeletePreview:
    def test_recursive_delete_enumerates_real_paths(self, tmp_path):
        target = tmp_path / "doomed"
        target.mkdir()
        (target / "a.txt").write_text("a", encoding="utf-8")
        (target / "b.txt").write_text("b", encoding="utf-8")

        # The path is quoted because it contains spaces, which is what an
        # operator actually types — and what the model should emit.
        preview = preview_impact("bash", {"command": f'rm -rf "{target}"'})

        assert preview.kind == ImpactKind.FILE_DELETE
        assert preview.measurable is True
        assert any(str(target) in p for p in preview.affected_paths)
        assert preview.evidence["targets"] == [str(target)]

    def test_delete_of_a_single_file_names_it(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("x", encoding="utf-8")

        preview = preview_impact("bash", {"command": f'rm "{victim}"'})

        assert preview.kind == ImpactKind.FILE_DELETE
        assert str(victim) in preview.affected_paths

    def test_unquoted_path_with_a_space_is_two_shell_operands(self):
        """Shell semantics, preserved: ``rm a b`` deletes two targets."""
        preview = preview_impact("bash", {"command": "rm -rf /tmp/one two"})
        assert preview.evidence["targets"] == ["/tmp/one", "two"]

    def test_delete_of_a_nonexistent_target_names_it_rather_than_saying_nothing(self):
        """A target that does not exist is still *named*, never "nothing here"."""
        preview = preview_impact("bash", {"command": "rm /definitely/not/here"})
        assert preview.measurable is True
        assert any("not found" in p for p in preview.affected_paths)

    def test_bare_rm_with_no_target_reports_no_targets(self):
        preview = preview_impact("bash", {"command": "rm"})
        assert preview.kind == ImpactKind.FILE_DELETE
        assert preview.measurable is False
        assert preview.reason == "NO_TARGETS"


class TestGitPreview:
    def test_forced_clean_names_untracked_removal(self):
        preview = preview_impact("bash", {"command": "git clean -fd"})
        assert preview.kind == ImpactKind.GIT_DISCARD
        assert preview.measurable is True
        assert "untracked" in preview.summary
        assert preview.evidence["forced"] is True

    def test_dry_run_clean_is_reported_as_making_no_change(self):
        preview = preview_impact("bash", {"command": "git clean -n"})
        assert preview.evidence["forced"] is False
        assert preview.reason == "DRY_RUN_NO_CHANGE"

    def test_hard_reset_names_the_working_tree(self):
        preview = preview_impact("bash", {"command": "git reset --hard HEAD~1"})
        assert preview.kind == ImpactKind.GIT_DISCARD
        assert preview.evidence["hard"] is True
        assert "uncommitted" in preview.summary

    def test_soft_reset_makes_no_working_tree_change(self):
        preview = preview_impact("bash", {"command": "git reset --soft HEAD~1"})
        assert preview.evidence["hard"] is False
        assert preview.reason == "NO_WORKING_TREE_CHANGE"

    def test_force_push_names_remote_history_rewrite(self):
        preview = preview_impact("bash", {"command": "git push origin main --force"})
        assert preview.reason == "REMOTE_HISTORY_REWRITE"


class TestSqlPreview:
    def test_drop_table_is_irreversible_and_explicit(self):
        preview = preview_impact("bash", {"command": "psql -c 'DROP TABLE users'"})
        assert preview.kind == ImpactKind.SQL_SCHEMA
        assert preview.evidence["target"] == "users"
        assert "permanently" in preview.summary

    def test_truncate_is_classified_the_same_way(self):
        preview = preview_impact("bash", {"command": "truncate table sessions"})
        assert preview.kind == ImpactKind.SQL_SCHEMA
        assert preview.evidence["target"] == "sessions"


class TestOtherPreviews:
    def test_install_names_packages_and_flags_third_party_code(self):
        preview = preview_impact("bash", {"command": "pip install requests numpy"})
        assert preview.kind == ImpactKind.PACKAGE_INSTALL
        assert preview.evidence["packages"] == ["requests", "numpy"]
        assert preview.reason == "THIRD_PARTY_CODE"

    def test_network_request_is_flagged_as_egress(self):
        preview = preview_impact("bash", {"command": "curl https://example.com/upload"})
        assert preview.kind == ImpactKind.NETWORK
        assert preview.reason == "NETWORK_EGRESS"

    def test_unclassified_command_is_not_measurable(self):
        preview = preview_impact("bash", {"command": "pytest -q"})
        assert preview.measurable is False
        assert preview.reason == "UNCLASSIFIED_COMMAND"

    def test_file_write_reports_target_and_size(self):
        preview = preview_impact("write_to_file", {"TargetFile": "src/app.py", "ReplacementContent": "print('hi')"})
        assert preview.kind == ImpactKind.FILE_WRITE
        assert preview.measurable is True
        assert preview.affected_paths == ("src/app.py",)
        assert preview.estimated_bytes == len("print('hi')")

    def test_write_of_a_script_containing_rm_is_reported_as_a_write(self):
        """The outer effect is the write; the rm is text, not an action."""
        preview = preview_impact("write_to_file", {"TargetFile": "cleanup.sh", "ReplacementContent": "rm -rf /tmp/x"})
        assert preview.kind == ImpactKind.FILE_WRITE
        assert preview.evidence == {}

    def test_unknown_tool_has_no_impact_model(self):
        preview = preview_impact("teleport_to_mars", {})
        assert preview.measurable is False
        assert preview.reason == "NO_IMPACT_MODEL"

    def test_preview_never_executes_anything(self):
        """A preview of a destructive command must not perform it."""
        import os

        before = os.listdir(".")
        preview = preview_impact("bash", {"command": "rm -rf ."})
        after = os.listdir(".")
        assert before == after
        assert preview.measurable is True


class TestPreviewSerialization:
    def test_to_dict_is_json_shaped(self):
        import json

        preview = preview_impact("bash", {"command": "git reset --hard"})
        payload = json.dumps(preview.to_dict())
        assert '"kind"' in payload
        assert '"measurable"' in payload
