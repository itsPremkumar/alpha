"""Tests for the command-content policy provider.

Covers the four properties that make this gate safe rather than decorative:

1. Structural role classification actually distinguishes pipeline stages from
   standalone commands.
2. A parse failure can never produce an allow.
3. Interpreters and inline-code runners are never auto-allowed, by any rule.
4. Quoted data is opaque, so a commit message is never pattern-matched.
"""

import pytest

from alpha.guardrails.command_policy import (
    PROFILES,
    CommandPolicyProvider,
    CommandRule,
    decompose,
    evaluate,
)
from alpha.guardrails.provider import GuardrailRequest

READ_ONLY = PROFILES["read-only"]
REPO_WRITE = PROFILES["repo-write"]


def _roles(command: str) -> dict[str, str]:
    return {fragment.executable: fragment.role for fragment in decompose(command).fragments}


class TestDecomposition:
    def test_simple_command(self) -> None:
        result = decompose("ls -la /mnt/user-data/workspace")
        assert result.complete
        assert len(result.fragments) == 1
        assert result.fragments[0].executable == "ls"
        assert result.fragments[0].role == "standalone"

    def test_pipeline_first_stage_is_standalone(self) -> None:
        result = decompose("gh pr list | head -20")
        assert result.complete
        assert [f.executable for f in result.fragments] == ["gh", "head"]
        assert result.fragments[0].role == "standalone"
        assert result.fragments[1].role == "pipe_filter"

    def test_and_or_chain_restarts_standalone(self) -> None:
        # `&&` begins a new pipeline, so `wc` is standalone again rather than
        # inheriting pipe_filter from an earlier stage.
        result = decompose("cat a.txt | wc -l && wc -l b.txt")
        assert result.complete
        assert [f.role for f in result.fragments] == ["standalone", "pipe_filter", "standalone"]

    def test_command_substitution_is_its_own_fragment(self) -> None:
        result = decompose("echo $(git rev-parse HEAD)")
        assert result.complete
        assert {f.executable for f in result.fragments} == {"echo", "git"}
        git = next(f for f in result.fragments if f.executable == "git")
        assert git.role == "substitution"

    def test_backtick_substitution(self) -> None:
        result = decompose("echo `git status`")
        assert result.complete
        assert {f.executable for f in result.fragments} == {"echo", "git"}

    def test_nested_substitution(self) -> None:
        result = decompose("echo $(echo $(git log --oneline))")
        assert result.complete
        assert "git" in {f.executable for f in result.fragments}

    def test_executable_normalization(self) -> None:
        assert decompose("/bin/rm -rf /tmp/x").fragments[0].executable == "rm"
        assert decompose("C:\\Windows\\System32\\cmd.exe /c dir").fragments[0].executable == "cmd"

    def test_windows_path_separators_survive_tokenizing(self) -> None:
        # Backslash is literal, not a POSIX escape, so a Windows path is not
        # silently rewritten into a different string than the shell receives.
        result = decompose(r"cat C:\Users\me\file.txt")
        assert result.complete
        assert result.fragments[0].argv == ("cat", r"C:\Users\me\file.txt")

    def test_redirection_is_separated_from_argv(self) -> None:
        result = decompose("ls > out.txt")
        assert result.complete
        fragment = result.fragments[0]
        assert fragment.argv == ("ls",)
        assert fragment.redirections == ((">", "out.txt"),)
        assert fragment.has_output_redirection

    def test_semicolon_separator(self) -> None:
        result = decompose("ls; pwd")
        assert result.complete
        assert [f.executable for f in result.fragments] == ["ls", "pwd"]

    def test_quoted_data_is_opaque(self) -> None:
        # A `>` or a `|` inside quotes must not be read as an operator, and the
        # quoted text must not be decomposed as commands.
        result = decompose("git commit -m 'fix rm -rf and | pipe'")
        assert result.complete
        assert len(result.fragments) == 1
        assert result.fragments[0].argv == ("git", "commit", "-m", "fix rm -rf and | pipe")

    def test_double_quoted_data_is_opaque(self) -> None:
        result = decompose('grep -n "a && b" file.txt')
        assert result.complete
        assert len(result.fragments) == 1
        assert result.fragments[0].argv[-2:] == ("a && b", "file.txt")

    def test_unterminated_quote_is_reported(self) -> None:
        result = decompose("echo 'unterminated")
        assert not result.complete
        assert any("quote" in problem for problem in result.problems)

    def test_unterminated_substitution_is_reported(self) -> None:
        result = decompose("echo $(git status")
        assert not result.complete

    def test_empty_command(self) -> None:
        assert decompose("").complete
        assert not decompose("   ").fragments

    def test_deep_substitution_nesting_is_reported(self) -> None:
        result = decompose("echo " + "$(" * 12 + "ls" + ")" * 12)
        assert not result.complete


class TestParseFailuresNeverAllow:
    def test_unparseable_command_defers(self) -> None:
        outcome = evaluate("echo 'unterminated", READ_ONLY)
        assert outcome.verdict == "defer"
        assert not outcome.allow
        assert "could not be fully parsed" in outcome.reason

    def test_unparseable_git_push_still_defers_not_allows(self) -> None:
        # Even a command whose prefix looks allowlisted must not be allowed when
        # the rest of it could not be parsed.
        outcome = evaluate("git status && echo 'oops", REPO_WRITE)
        assert outcome.verdict == "defer"
        assert not outcome.allow


class TestStructuralRoles:
    def test_filter_in_pipeline_is_allowed_but_standalone_is_not(self) -> None:
        # This is the case a naive "is head safe?" gate gets wrong in one
        # direction or the other. Role classification gets both right.
        allowed = evaluate("gh pr list | head -20", READ_ONLY)
        assert allowed.allow, allowed.reason

        standalone = evaluate("head /etc/passwd", READ_ONLY)
        assert not standalone.allow
        assert standalone.verdict == "defer"

    def test_first_pipeline_stage_is_standalone_not_pipe_filter(self) -> None:
        # `wc -l f.txt | cat` has `wc` in command position, so the pipeline-only
        # rule does not apply to it.
        assert evaluate("wc -l f.txt | cat", READ_ONLY).verdict == "defer"
        assert evaluate("cat f.txt | wc -l", READ_ONLY).allow

    def test_substitution_role_is_allowed(self) -> None:
        assert evaluate("echo $(wc -l file.txt)", READ_ONLY).allow


class TestInterpreterSafety:
    def test_interpreter_never_auto_allowed(self) -> None:
        for command in (
            "bash -c 'ls'",
            "sh -c 'rm -rf /'",
            "pwsh -Command 'Get-ChildItem'",
            "cmd /c dir",
            "sudo ls",
            "xargs rm",
            "env FOO=1 ls",
        ):
            outcome = evaluate(command, READ_ONLY)
            assert not outcome.allow, f"{command!r} was allowed: {outcome.reason}"
            assert outcome.verdict == "defer", command

    def test_interpreter_cannot_be_outranked_by_an_allow_rule(self) -> None:
        # An operator who allowlists bash must still not get an auto-allow.
        permissive = (CommandRule(executables=["bash", "sh", "sudo"], verdict="allow"),)
        assert not evaluate("bash -c 'ls'", permissive).allow
        assert evaluate("bash -c 'ls'", permissive).verdict == "defer"

    def test_inline_exec_flag_defers(self) -> None:
        assert not evaluate("python -c 'import os; os.system(\"ls\")'", READ_ONLY).allow
        assert not evaluate("node -e 'require(\"fs\")'", READ_ONLY).allow

    def test_script_invocation_defers_as_an_interpreter(self) -> None:
        # `python` is an interpreter, so the interpreter rule wins over the
        # project-code rule and the verdict is `defer`, not `ask`.
        outcome = evaluate("python build.py", READ_ONLY)
        assert outcome.verdict == "defer"

    def test_non_interpreter_project_code_asks(self) -> None:
        # `make` is not an interpreter, so it reaches the project-code rule.
        assert evaluate("make build", READ_ONLY).verdict == "ask"


class TestReadOnlyProfile:
    def test_reads_are_allowed(self) -> None:
        for command in ("ls -la", "cat README.md", "grep -r foo .", "find . -name '*.py'", "jq . a.json", "cat f.txt | wc -l"):
            outcome = evaluate(command, READ_ONLY)
            assert outcome.allow, f"{command!r}: {outcome.reason}"

    def test_output_shapers_are_pipeline_only(self) -> None:
        # `wc` reads a file exactly like `cat` does, so restricting it to a
        # pipeline loses nothing: the model can use `cat` when it wants the
        # file itself. The restriction exists to prove out the role mechanism.
        assert evaluate("wc -l f.txt", READ_ONLY).verdict == "defer"
        assert evaluate("cat f.txt | wc -l", READ_ONLY).allow

    def test_git_read_subcommands_are_allowed(self) -> None:
        for command in ("git status", "git log --oneline", "git diff HEAD~1", "git show abc123", "git blame f.py"):
            assert evaluate(command, READ_ONLY).allow, command

    def test_gh_read_subcommands_are_allowed(self) -> None:
        for command in ("gh pr list", "gh pr view 12", "gh issue list", "gh repo view", "gh run list"):
            assert evaluate(command, READ_ONLY).allow, command

    def test_gh_api_defers_because_it_can_write(self) -> None:
        # `gh api` reads and deletes with the same spelling, so it must not be
        # auto-allowed by a subcommand allowlist.
        assert evaluate("gh api repos/o/r", READ_ONLY).verdict == "defer"

    def test_git_write_asks_under_read_only(self) -> None:
        for command in ("git commit -m x", "git push", "git reset --hard", "git clean -fd", "git stash pop"):
            outcome = evaluate(command, READ_ONLY)
            assert not outcome.allow, command
            assert outcome.verdict == "ask", command

    def test_project_code_execution_asks(self) -> None:
        for command in ("make build", "npm test", "pip install requests", "pytest -q"):
            outcome = evaluate(command, READ_ONLY)
            assert outcome.verdict == "ask", command

    def test_dangerous_rm_defers_because_it_is_unclassified(self) -> None:
        # `rm` is intentionally not in the read-only set, and is not hard-denied
        # either: it becomes a human decision rather than a brick wall.
        outcome = evaluate("rm -rf build", READ_ONLY)
        assert outcome.verdict == "defer"

    def test_hard_deny_is_small_and_named(self) -> None:
        outcome = evaluate("mkfs.ext4 /dev/sda1", READ_ONLY)
        assert outcome.verdict == "deny"
        assert "filesystem" in outcome.reason

    def test_hard_deny_matches_versioned_variants(self) -> None:
        # `mkfs` must cover `mkfs.xfs`/`mkfs.btrfs` without matching an
        # unrelated tool that merely shares a prefix.
        assert evaluate("mkfs.xfs /dev/sdb1", READ_ONLY).verdict == "deny"
        assert evaluate("mkfsmytool --help", READ_ONLY).verdict != "deny"


class TestRepoWriteProfile:
    def test_read_only_behaviour_is_a_subset(self) -> None:
        # repo-write must never be stricter than read-only on a read.
        for command in ("ls", "git status", "cat f.txt | head -5"):
            assert evaluate(command, REPO_WRITE).allow, command

    def test_git_mutations_ask_rather_than_deny(self) -> None:
        for command in ("git add .", "git commit -m x", "git switch main", "git merge feature"):
            assert evaluate(command, REPO_WRITE).verdict == "ask", command

    def test_force_push_asks_and_names_the_alternative(self) -> None:
        outcome = evaluate("git push --force origin main", REPO_WRITE)
        assert outcome.verdict == "ask"
        assert "force-with-lease" in outcome.reason

    def test_force_with_lease_is_not_the_force_rule(self) -> None:
        outcome = evaluate("git push --force-with-lease origin main", REPO_WRITE)
        assert outcome.verdict == "ask"  # still a write, but not the force rule
        assert "force-with-lease" not in outcome.reason


class TestComposition:
    def test_any_deny_wins_over_ask_and_allow(self) -> None:
        rules = (
            CommandRule(executables=["ls"], verdict="allow"),
            CommandRule(executables=["mkfs"], verdict="deny"),
        )
        assert evaluate("ls", rules).allow
        assert evaluate("mkfs /dev/sda", rules).verdict == "deny"

    def test_any_fragment_denying_denies_the_whole_command(self) -> None:
        outcome = evaluate("ls && mkfs /dev/sda1", REPO_WRITE)
        assert outcome.verdict == "deny"

    def test_ask_beats_allow(self) -> None:
        rules = (
            CommandRule(executables=["git"], has_any=["commit"], verdict="ask"),
            CommandRule(executables=["git"], verdict="allow"),
        )
        assert evaluate("git commit -m x", rules).verdict == "ask"
        assert evaluate("git status", rules).allow

    def test_rule_order_does_not_change_the_verdict(self) -> None:
        forward = (
            CommandRule(executables=["git"], has_any=["commit"], verdict="ask"),
            CommandRule(executables=["git"], verdict="allow"),
        )
        reverse = tuple(reversed(forward))
        assert evaluate("git commit -m x", forward).verdict == evaluate("git commit -m x", reverse).verdict == "ask"

    def test_unless_suppresses_a_match(self) -> None:
        rules = (CommandRule(executables=["curl"], has_any=["-L"], unless=["--safe"], verdict="deny"),)
        assert evaluate("curl -L http://x", rules).verdict == "deny"
        assert evaluate("curl -L --safe http://x", rules).verdict == "defer"

    def test_has_all_requires_every_token(self) -> None:
        rules = (CommandRule(executables=["rm"], has_all=["-rf", "--no-preserve-root"], verdict="deny"),)
        assert evaluate("rm -rf --no-preserve-root /", rules).verdict == "deny"
        # Missing the second token, so the rule does not match and the command
        # falls through to `defer` rather than being denied.
        assert evaluate("rm -rf /", rules).verdict == "defer"

    def test_empty_command_defers(self) -> None:
        assert evaluate("", READ_ONLY).verdict == "defer"


class TestFalsePositives:
    def test_commit_message_mentioning_danger_is_allowed_to_reach_rules(self) -> None:
        # The whole point of structural parsing: the quoted message is one token
        # and never inspected. `git commit` still asks (it is a write), but the
        # reason must be "mutates the repository", not a bogus rm detection.
        outcome = evaluate("git commit -m 'fix rm -rf bug in cleanup'", REPO_WRITE)
        assert outcome.verdict == "ask"
        assert outcome.reason == "mutates the repository"

    def test_rm_inside_a_commit_message_does_not_deny(self) -> None:
        rules = (CommandRule(executables=["rm"], has_any=["-rf"], verdict="deny"),)
        outcome = evaluate("git commit -m 'remove rm -rf from docs'", rules)
        assert outcome.verdict == "defer"  # git unclassified here, not denied

    def test_grep_pattern_containing_shell_metacharacters(self) -> None:
        outcome = evaluate("grep -rn 'rm -rf' .", READ_ONLY)
        assert outcome.allow, outcome.reason


class TestProvider:
    def _request(self, command: object, tool: str = "bash") -> GuardrailRequest:
        return GuardrailRequest(tool_name=tool, tool_input={"command": command} if isinstance(command, str) else {})

    def test_unguarded_tool_is_untouched(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        assert provider.evaluate(self._request("mkfs /dev/sda", tool="read_file")).allow

    def test_guarded_tool_is_evaluated(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        assert not provider.evaluate(self._request("mkfs /dev/sda")).allow

    def test_missing_command_argument_is_refused(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        decision = provider.evaluate(GuardrailRequest(tool_name="bash", tool_input={}))
        assert not decision.allow
        assert decision.reasons[0].code == "oap.command_missing"

    def test_non_string_command_is_refused(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        decision = provider.evaluate(GuardrailRequest(tool_name="bash", tool_input={"command": ["ls"]}))
        assert not decision.allow

    def test_verdict_is_preserved_in_metadata(self) -> None:
        # GuardrailDecision.allow is binary, so ASK and DEFER both surface as a
        # block. The real verdict must survive for a future approval UI.
        provider = CommandPolicyProvider(profile="read-only")
        ask = provider.evaluate(self._request("git commit -m x"))
        defer = provider.evaluate(self._request("rm -rf build"))
        assert not ask.allow and not defer.allow
        assert ask.metadata["verdict"] == "ask"
        assert defer.metadata["verdict"] == "defer"
        assert ask.reasons[0].code == "oap.command_needs_approval"
        assert defer.reasons[0].code == "oap.command_unclassified"

    def test_deny_reason_code(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        decision = provider.evaluate(self._request("mkfs /dev/sda1"))
        assert decision.reasons[0].code == "oap.command_denied"

    def test_unknown_profile_is_rejected(self) -> None:
        try:
            CommandPolicyProvider(profile="does-not-exist")
        except ValueError as exc:
            assert "does-not-exist" in str(exc)
        else:
            raise AssertionError("expected ValueError for an unknown profile")

    def test_explicit_empty_guarded_tools_disables_gating(self) -> None:
        # None means "bash only"; [] is an explicit "gate nothing". A truthiness
        # test would collapse [] into None and silently re-enable gating.
        provider = CommandPolicyProvider(profile="read-only", guarded_tools=[])
        assert provider.evaluate(self._request("mkfs /dev/sda")).allow

    def test_custom_guarded_tools(self) -> None:
        provider = CommandPolicyProvider(profile="read-only", guarded_tools=["run_shell"])
        assert provider.evaluate(self._request("mkfs /dev/sda", tool="run_shell")).allow is False
        assert provider.evaluate(self._request("mkfs /dev/sda", tool="bash")).allow is True

    def test_release_policy_parameters_are_serializable(self) -> None:
        params = CommandPolicyProvider(profile="repo-write").release_policy_parameters()
        assert params["profile"] == "repo-write"
        assert params["guarded_tools"] == ["bash"]
        assert isinstance(params["rule_count"], int)

    @pytest.mark.asyncio
    async def test_aevaluate_matches_evaluate(self) -> None:
        provider = CommandPolicyProvider(profile="read-only")
        request = self._request("ls")
        assert (await provider.aevaluate(request)).allow == provider.evaluate(request).allow
