"""Tests for user-scoped path resolution in Paths."""

from pathlib import Path

import pytest

from alpha.config.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path)


class TestValidateUserId:
    def test_valid_user_id(self, paths: Paths):
        d = paths.user_dir("u-abc-123")
        assert d == paths.base_dir / "users" / "u-abc-123"

    def test_rejects_path_traversal(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("../escape")

    def test_rejects_slash(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("foo/bar")

    def test_rejects_empty(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("")


class TestValidateUserIdRejectsTrailingNewline:
    """`$` in a Python regex also matches just before a trailing newline.

    `_SAFE_USER_ID_RE` is `^[A-Za-z0-9_\\-]+$` and was applied with
    `re.match`, which anchors only the start. So `"alice\\n"` matched, the
    validator returned the id unchanged, and `Paths.user_dir` produced a
    directory literally named ``alice\\n`` from the one function whose
    docstring says it validates "before using it in filesystem paths".

    `alpha/utils/thread_id.py::validate_thread_id` gets this right with
    `fullmatch`, so this is an inconsistency rather than a policy choice.
    """

    @pytest.mark.parametrize(
        "user_id",
        [
            "alice\n",
            "alice\r",
            "alice\r\n",
            "alice\t",
            "alice ",
            "alice\n\n",
            "alice\x0b",
            "alice\x0c",
        ],
    )
    def test_rejects_trailing_control_and_whitespace(self, paths: Paths, user_id):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir(user_id)

    @pytest.mark.parametrize("user_id", ["alice\n", "alice\r\n"])
    def test_user_dir_never_yields_a_directory_with_a_raw_newline(self, paths: Paths, user_id):
        """The failure is a filesystem effect, not just a return value."""
        with pytest.raises(ValueError):
            d = paths.user_dir(user_id)
            raise AssertionError(f"user_dir accepted {user_id!r} and returned {d!r}")

    @pytest.mark.parametrize("user_id", ["alice\n", "alice\r\n"])
    def test_thread_sibling_validator_agrees(self, user_id):
        """Pin the cross-module contract, not just this regex.

        If a future change makes these two disagree again, this fails.
        """
        from alpha.utils.thread_id import validate_thread_id

        with pytest.raises(ValueError, match="Invalid thread_id"):
            validate_thread_id(user_id)

    @pytest.mark.parametrize("user_id", ["alice", "u-abc-123", "ABC_123", "a", "9"])
    def test_valid_ids_are_still_accepted(self, paths: Paths, user_id):
        """The guard must not over-tighten and start rejecting legal ids."""
        assert paths.user_dir(user_id) == paths.base_dir / "users" / user_id


class TestMakeSafeUserId:
    def test_already_safe_id_is_unchanged(self):
        from alpha.config.paths import make_safe_user_id

        assert make_safe_user_id("ou_abc-123") == "ou_abc-123"
        assert make_safe_user_id("123456") == "123456"

    def test_unsafe_chars_are_sanitized_with_stable_suffix(self):
        from alpha.config.paths import make_safe_user_id

        result = make_safe_user_id("user@example.com")
        # Sanitized prefix plus a stable digest of the original.
        assert result.startswith("user-example-com-")
        assert len(result.rsplit("-", 1)[1]) == 16
        assert result == "user-example-com-b4c9a289323b21a0"
        assert make_safe_user_id("user@example.com") == result

    def test_sanitized_id_passes_validation(self, paths: Paths):
        from alpha.config.paths import make_safe_user_id

        safe = make_safe_user_id("用户/../etc")
        # Must be usable as a filesystem-scoped bucket without raising.
        assert paths.user_dir(safe) == paths.base_dir / "users" / safe

    def test_distinct_unsafe_ids_do_not_collide(self):
        from alpha.config.paths import make_safe_user_id

        assert make_safe_user_id("a.b") != make_safe_user_id("a/b")

    def test_empty_id_rejected(self):
        from alpha.config.paths import make_safe_user_id

        with pytest.raises(ValueError, match="non-empty"):
            make_safe_user_id("")


class TestValidateIntegrationId:
    def test_accepts_dotted_integration_id(self):
        from alpha.config.paths import _validate_integration_id

        assert _validate_integration_id("lark-cli") == "lark-cli"
        assert _validate_integration_id("some.integration") == "some.integration"

    @pytest.mark.parametrize("integration_id", [".", ".."])
    def test_rejects_dot_and_dotdot(self, integration_id):
        from alpha.config.paths import _validate_integration_id

        with pytest.raises(ValueError, match="Invalid integration_id"):
            _validate_integration_id(integration_id)

    @pytest.mark.parametrize("integration_id", [".", ".."])
    def test_host_integration_config_dir_rejects_dot_traversal(self, paths: Paths, integration_id):
        with pytest.raises(ValueError, match="Invalid integration_id"):
            paths.host_user_integration_config_dir("alice", integration_id)

    @pytest.mark.parametrize("integration_id", [".", ".."])
    def test_host_integration_data_dir_rejects_dot_traversal(self, paths: Paths, integration_id):
        with pytest.raises(ValueError, match="Invalid integration_id"):
            paths.host_user_integration_data_dir("alice", integration_id)

    @pytest.mark.parametrize(
        "integration_id",
        ["some.integration\n", "lark-cli\n", "..\n", ".\n", "..\r\n", "x.integration\t", "x.integration "],
    )
    def test_rejects_trailing_control_and_whitespace(self, integration_id):
        """Same `$`-before-trailing-newline hole as the user-id validator."""
        from alpha.config.paths import _validate_integration_id

        with pytest.raises(ValueError, match="Invalid integration_id"):
            _validate_integration_id(integration_id)

    @pytest.mark.parametrize("integration_id", ["..\n", ".\n"])
    def test_dot_traversal_guard_cannot_be_evaded_with_a_newline(self, integration_id):
        """`'..\\n'` is not in `{'.', '..'}`, so the explicit guard alone misses it.

        This is the concrete consequence of the regex hole: the traversal
        guard is a literal set membership test, and a trailing byte makes the
        id a different string that still resolves inside the namespace.
        """
        from alpha.config.paths import _validate_integration_id

        with pytest.raises(ValueError, match="Invalid integration_id"):
            _validate_integration_id(integration_id)

    @pytest.mark.parametrize(
        "integration_id",
        ["lark-cli", "some.integration", "a.b.c", "A_b-1.2", "x"],
    )
    def test_valid_integration_ids_are_still_accepted(self, integration_id):
        from alpha.config.paths import _validate_integration_id

        assert _validate_integration_id(integration_id) == integration_id


class TestUserDir:
    def test_user_dir(self, paths: Paths):
        assert paths.user_dir("alice") == paths.base_dir / "users" / "alice"

    def test_prepare_user_dir_migrates_unique_legacy_unsafe_bucket(self, paths: Paths):
        from alpha.config.paths import make_safe_user_id

        raw = "user@example.com"
        safe = make_safe_user_id(raw)
        legacy_dir = paths.base_dir / "users" / "user-example-com-63a710569261a24b"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "memory.json").write_text('{"legacy": true}\n', encoding="utf-8")

        assert paths.prepare_user_dir_for_raw_id(raw) == safe

        current_dir = paths.user_dir(safe)
        assert current_dir.exists()
        assert not legacy_dir.exists()
        assert (current_dir / "memory.json").read_text(encoding="utf-8") == '{"legacy": true}\n'

    def test_prepare_user_dir_never_migrates_another_users_bucket(self, paths: Paths):
        """A different raw ID with the same sanitized prefix has a different legacy digest."""
        import hashlib

        from alpha.config.paths import make_safe_user_id

        users_dir = paths.base_dir / "users"
        other_legacy = users_dir / f"a-b-{hashlib.sha1(b'a/b').hexdigest()[:16]}"
        other_legacy.mkdir(parents=True)
        arbitrary_16_hex = users_dir / "a-b-1111111111111111"
        arbitrary_16_hex.mkdir(parents=True)

        assert paths.prepare_user_dir_for_raw_id("a.b") == make_safe_user_id("a.b")

        assert not paths.user_dir(make_safe_user_id("a.b")).exists()
        assert other_legacy.exists()
        assert arbitrary_16_hex.exists()


class TestUserMemoryFile:
    def test_user_memory_file(self, paths: Paths):
        assert paths.user_memory_file("bob") == paths.base_dir / "users" / "bob" / "memory.json"


class TestUserAgentMemoryFile:
    def test_user_agent_memory_file(self, paths: Paths):
        expected = paths.base_dir / "users" / "bob" / "agents" / "myagent" / "memory.json"
        assert paths.user_agent_memory_file("bob", "myagent") == expected

    def test_user_agent_memory_file_lowercases_name(self, paths: Paths):
        expected = paths.base_dir / "users" / "bob" / "agents" / "myagent" / "memory.json"
        assert paths.user_agent_memory_file("bob", "MyAgent") == expected


class TestUserAgentDir:
    def test_user_agents_dir(self, paths: Paths):
        assert paths.user_agents_dir("alice") == paths.base_dir / "users" / "alice" / "agents"

    def test_user_agent_dir(self, paths: Paths):
        assert paths.user_agent_dir("alice", "code-reviewer") == paths.base_dir / "users" / "alice" / "agents" / "code-reviewer"

    def test_user_agent_dir_lowercases_name(self, paths: Paths):
        assert paths.user_agent_dir("alice", "CodeReviewer") == paths.base_dir / "users" / "alice" / "agents" / "codereviewer"

    def test_user_agent_dir_validates_user_id(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_agent_dir("../escape", "myagent")


class TestUserThreadDir:
    def test_user_thread_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1"
        assert paths.thread_dir("t1", user_id="u1") == expected

    def test_thread_dir_no_user_id_falls_back_to_legacy(self, paths: Paths):
        expected = paths.base_dir / "threads" / "t1"
        assert paths.thread_dir("t1") == expected


class TestUserSandboxDirs:
    def test_sandbox_work_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "workspace"
        assert paths.sandbox_work_dir("t1", user_id="u1") == expected

    def test_sandbox_uploads_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "uploads"
        assert paths.sandbox_uploads_dir("t1", user_id="u1") == expected

    def test_sandbox_outputs_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "outputs"
        assert paths.sandbox_outputs_dir("t1", user_id="u1") == expected

    def test_sandbox_user_data_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data"
        assert paths.sandbox_user_data_dir("t1", user_id="u1") == expected

    def test_acp_workspace_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "acp-workspace"
        assert paths.acp_workspace_dir("t1", user_id="u1") == expected

    def test_legacy_sandbox_work_dir(self, paths: Paths):
        expected = paths.base_dir / "threads" / "t1" / "user-data" / "workspace"
        assert paths.sandbox_work_dir("t1") == expected


class TestHostPathsWithUserId:
    def test_host_thread_dir_with_user_id(self, paths: Paths):
        result = paths.host_thread_dir("t1", user_id="u1")
        assert "users" in result
        assert "u1" in result
        assert "threads" in result
        assert "t1" in result

    def test_host_thread_dir_legacy(self, paths: Paths):
        result = paths.host_thread_dir("t1")
        assert "threads" in result
        assert "t1" in result
        assert "users" not in result

    def test_host_sandbox_user_data_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_user_data_dir("t1", user_id="u1")
        assert "users" in result
        assert "user-data" in result

    def test_host_sandbox_work_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_work_dir("t1", user_id="u1")
        assert "workspace" in result

    def test_host_sandbox_uploads_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_uploads_dir("t1", user_id="u1")
        assert "uploads" in result

    def test_host_sandbox_outputs_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_outputs_dir("t1", user_id="u1")
        assert "outputs" in result

    def test_host_acp_workspace_dir_with_user_id(self, paths: Paths):
        result = paths.host_acp_workspace_dir("t1", user_id="u1")
        assert "acp-workspace" in result


class TestEnsureAndDeleteWithUserId:
    def test_ensure_thread_dirs_creates_user_scoped(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        assert paths.sandbox_work_dir("t1", user_id="u1").is_dir()
        assert paths.sandbox_uploads_dir("t1", user_id="u1").is_dir()
        assert paths.sandbox_outputs_dir("t1", user_id="u1").is_dir()
        assert paths.acp_workspace_dir("t1", user_id="u1").is_dir()

    def test_delete_thread_dir_removes_user_scoped(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        assert paths.thread_dir("t1", user_id="u1").exists()
        paths.delete_thread_dir("t1", user_id="u1")
        assert not paths.thread_dir("t1", user_id="u1").exists()

    def test_delete_thread_dir_idempotent(self, paths: Paths):
        paths.delete_thread_dir("nonexistent", user_id="u1")  # should not raise

    def test_ensure_thread_dirs_legacy_still_works(self, paths: Paths):
        paths.ensure_thread_dirs("t1")
        assert paths.sandbox_work_dir("t1").is_dir()

    def test_user_scoped_and_legacy_are_independent(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        paths.ensure_thread_dirs("t1")
        # Both exist independently
        assert paths.thread_dir("t1", user_id="u1").exists()
        assert paths.thread_dir("t1").exists()
        # Delete one doesn't affect the other
        paths.delete_thread_dir("t1", user_id="u1")
        assert not paths.thread_dir("t1", user_id="u1").exists()
        assert paths.thread_dir("t1").exists()


class TestResolveVirtualPathWithUserId:
    def test_resolve_virtual_path_with_user_id(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        result = paths.resolve_virtual_path("t1", "/mnt/user-data/workspace/file.txt", user_id="u1")
        expected_base = paths.sandbox_user_data_dir("t1", user_id="u1").resolve()
        assert str(result).startswith(str(expected_base))

    def test_resolve_virtual_path_legacy(self, paths: Paths):
        paths.ensure_thread_dirs("t1")
        result = paths.resolve_virtual_path("t1", "/mnt/user-data/workspace/file.txt")
        expected_base = paths.sandbox_user_data_dir("t1").resolve()
        assert str(result).startswith(str(expected_base))


class TestAgentNameCannotEscapeTheAgentsNamespace:
    """`agent_dir` used to interpolate the name straight into the path.

    Validation existed, but it lived in `agents_config.validate_agent_name` — a
    different module. Every direct caller of the three path builders here
    bypassed it, so this was measured on the pre-fix code:

        Paths(base).agent_dir("../../escape")  -> {base}/agents/../../escape
        Paths(base).agent_dir("..")             -> {base}/agents/..
        Paths(base).agent_dir("a/b")            -> {base}/agents/a/b

    The first escapes the `agents/` namespace entirely; the third manufactures an
    extra directory level inside it. The module whose stated job is "validate an
    identifier before it becomes a filesystem path segment" had no agent-name
    check at all, so the security property depended on which caller you reached
    it through.
    """

    @pytest.mark.parametrize("name", ["researcher", "data-scientist", "a1", "A1", "x"])
    def test_ordinary_agent_names_are_unaffected(self, paths: Paths, name: str):
        assert paths.agent_dir(name) == paths.agents_dir / name.lower()

    @pytest.mark.parametrize(
        "name",
        [
            "../../escape",
            "..",
            ".",
            "a/b",
            "a\\b",
            "/abs",
            "sub/../../out",
            "..\\..\\out",
            "na me",
            "",
        ],
    )
    def test_rejects_traversal_and_separators(self, paths: Paths, name: str):
        with pytest.raises(ValueError, match="Invalid agent name"):
            paths.agent_dir(name)

    def test_user_agent_dir_is_guarded_too(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid agent name"):
            paths.user_agent_dir("u1", "../../escape")

    def test_managed_subagent_file_is_guarded_too(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid agent name"):
            paths.managed_subagent_file("../../escape")

    def test_the_path_layer_is_never_more_permissive_than_the_config_layer(self, paths: Paths):
        """`agents_config` is the API/config surface; `paths` builds directories.

        They are allowed to disagree, but only in one direction. The path layer
        adds bounds the config surface has no reason to know about — a 64-char
        segment cap and the Windows reserved-device check — so a name the
        config layer accepts can still be refused as a *directory*. The
        unacceptable direction is the other one: the path layer accepting a name
        the config layer rejects, which would let an unvalidated caller build a
        path the validated caller cannot.

        Compared behaviourally rather than by `.pattern` text, because the two
        spell the same charset differently (`^[A-Za-z0-9-]+$` vs
        `^[A-Za-z0-9\\-]+$`) and a string comparison would fail on that escaping
        alone while proving nothing about the contract.
        """
        from alpha.config.agents_config import validate_agent_name

        probes = [
            "researcher",
            "data-scientist",
            "a1",
            "A1",
            "x",
            "a_b",
            "a.b",
            "a b",
            "a/b",
            "../x",
            "",
            "CON",
            "nul",
            "x" * 65,
            "x" * 300,
        ]
        for probe in probes:
            try:
                validate_agent_name(probe)
                config_accepts = True
            except ValueError:
                config_accepts = False
            try:
                paths.agent_dir(probe)
                paths_accepts = True
            except ValueError:
                paths_accepts = False
            if paths_accepts and not config_accepts:
                pytest.fail(f"paths accepts {probe[:20]!r} but agents_config.validate_agent_name rejects it")
            if config_accepts and not paths_accepts:
                # Expected and safe: a filesystem bound the config surface has no
                # reason to enforce. Name the case so a new one is a deliberate
                # decision rather than an accident.
                assert len(probe) > 64 or probe.lower() in {"con", "nul"}, f"unexpected extra refusal for {probe[:20]!r}"


class TestReservedWindowsDeviceNames:
    """A reserved device name cannot be a directory on Windows.

    `CON`, `NUL`, `PRN`, `AUX` and `COM1`-`COM9`/`LPT1`-`LPT9` are reserved in
    *every* directory, not just at the drive root, and matched
    case-insensitively. Accepting such an id only defers the failure to an opaque
    `OSError` from a later `mkdir`, so the id is refused up front with a reason
    that names the cause.

    The check runs on every platform, not only Windows: an id that is impossible
    on Windows is a latent portability bug, and an id must not mean different
    things depending on which host booted.
    """

    @pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "PrN", "AUX", "COM1", "com9", "LPT1", "lpt9"])
    def test_reserved_user_ids_are_refused(self, paths: Paths, reserved: str):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir(reserved)

    @pytest.mark.parametrize("reserved", ["CON", "nul", "COM1"])
    def test_reserved_integration_ids_are_refused(self, paths: Paths, reserved: str):
        from alpha.config.paths import _validate_integration_id

        with pytest.raises(ValueError, match="Invalid integration_id"):
            _validate_integration_id(reserved)

    @pytest.mark.parametrize("reserved", ["CON", "nul", "COM1"])
    def test_reserved_agent_names_are_refused(self, paths: Paths, reserved: str):
        with pytest.raises(ValueError, match="Invalid agent name"):
            paths.agent_dir(reserved)

    @pytest.mark.parametrize("legit", ["console", "com10", "lpt", "nullable", "auxiliary", "conduit"])
    def test_names_that_merely_start_with_a_reserved_word_are_allowed(self, paths: Paths, legit: str):
        # Windows only reserves an exact match, so over-matching here would
        # refuse ordinary ids.
        assert paths.user_dir(legit).name == legit


class TestIdLengthIsBounded:
    """An unbounded id overflows MAX_PATH once the namespace is prepended.

    `thread_id` was already capped at 64 by `alpha.utils.thread_id`;
    `user_id` and `integration_id` had no cap at all. A 300-character id
    produced a 357-character path that no Windows API could open, and the
    failure surfaced as an unrelated `OSError` rather than a rejected id.
    """

    def test_user_dir_accepts_the_maximum_length(self, paths: Paths):
        assert paths.user_dir("u" * 64).name == "u" * 64

    def test_user_dir_refuses_beyond_the_maximum(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("u" * 65)

    def test_integration_id_refuses_beyond_the_maximum(self, paths: Paths):
        from alpha.config.paths import _validate_integration_id

        with pytest.raises(ValueError, match="Invalid integration_id"):
            _validate_integration_id("i" * 65)

    def test_agent_name_refuses_beyond_the_maximum(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid agent name"):
            paths.agent_dir("a" * 65)

    def test_the_error_names_the_limit_and_the_actual_length(self, paths: Paths):
        with pytest.raises(ValueError, match="at most 64 characters, got 300"):
            paths.user_dir("u" * 300)
