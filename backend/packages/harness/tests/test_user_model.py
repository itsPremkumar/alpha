"""Tests for the user-model provider ABC and implementations."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alpha.agents.memory.user_model import (
    FileUserModelProvider,
    NullUserModelProvider,
    UserModelProvider,
    create_user_model_provider,
)
from alpha.config.app_config import AppConfig


class TestNullUserModelProvider:
    """Tests for the null provider (byte-identical guarantee)."""

    @pytest.fixture
    def provider(self):
        return NullUserModelProvider()

    @pytest.fixture
    def runtime(self):
        rt = MagicMock()
        rt.context = {"thread_id": "test-thread"}
        return rt

    @pytest.fixture
    def app_config(self):
        return MagicMock(spec=AppConfig)

    @pytest.mark.asyncio
    async def test_initialize_noop(self, provider, runtime, app_config):
        await provider.initialize(runtime, app_config)
        # No exception

    def test_system_prompt_block_returns_none(self, provider, runtime):
        assert provider.system_prompt_block(runtime) is None

    @pytest.mark.asyncio
    async def test_prefetch_returns_empty(self, provider, runtime):
        result = await provider.prefetch(runtime)
        assert result == {}

    @pytest.mark.asyncio
    async def test_sync_turn_noop(self, provider, runtime):
        await provider.sync_turn(runtime, {"foo": "bar"})
        # No exception

    @pytest.mark.asyncio
    async def test_handle_tool_call_noop(self, provider, runtime):
        await provider.handle_tool_call(runtime, "bash", {"command": "ls"}, "output")
        # No exception

    @pytest.mark.asyncio
    async def test_shutdown_noop(self, provider):
        await provider.shutdown()
        # No exception


class TestFileUserModelProvider:
    """Tests for the file-backed dialectic provider."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    @pytest.fixture
    def provider(self, temp_dir):
        return FileUserModelProvider(storage_path=temp_dir)

    @pytest.fixture
    def runtime(self):
        rt = MagicMock()
        rt.context = {"thread_id": "test-thread"}
        return rt

    @pytest.fixture
    def app_config(self):
        return MagicMock(spec=AppConfig)

    @pytest.mark.asyncio
    async def test_initialize_loads_existing_entries(self, provider, runtime, app_config, temp_dir):
        # Pre-populate a user file
        user_file = temp_dir / "user_model_test_user.jsonl"
        entries = [
            {"turn": 1, "observation": "read file", "reflection": "user likes reading"},
            {"turn": 2, "observation": "write file", "reflection": "user writes code"},
        ]
        with user_file.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        runtime.context = {"thread_id": "test-thread"}
        with patch("alpha.agents.memory.user_model.resolve_runtime_user_id", return_value="test_user"):
            await provider.initialize(runtime, app_config)

        assert provider._user_id == "test_user"
        assert len(provider._entries) == 2
        assert provider._entries[0]["turn"] == 1

    @pytest.mark.asyncio
    async def test_initialize_creates_new_file(self, provider, runtime, app_config):
        runtime.context = {"thread_id": "test-thread"}
        with patch("alpha.agents.memory.user_model.resolve_runtime_user_id", return_value="new_user"):
            await provider.initialize(runtime, app_config)

        assert provider._user_id == "new_user"
        assert provider._entries == []

    def test_system_prompt_block_none_when_no_entries(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = []
        assert provider.system_prompt_block(runtime) is None

    def test_system_prompt_block_returns_summary(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = [
            {"turn": 1, "observation": "read file", "reflection": "user likes reading"},
            {"turn": 2, "observation": "write file", "reflection": "user writes code"},
        ]
        block = provider.system_prompt_block(runtime)
        assert block is not None
        assert "USER MODEL CONTEXT" in block
        assert "read file" in block
        assert "write file" in block
        assert "Turn 1" in block
        assert "Turn 2" in block

    def test_system_prompt_block_limits_to_recent(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = [{"turn": i, "observation": f"obs{i}", "reflection": f"ref{i}"} for i in range(15)]
        block = provider.system_prompt_block(runtime)
        assert block is not None
        # Should only include last 10
        assert "Turn 5" in block
        assert "Turn 4" not in block

    @pytest.mark.asyncio
    async def test_prefetch_returns_recent_entries(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = [{"turn": i, "observation": f"obs{i}", "reflection": f"ref{i}"} for i in range(5)]
        result = await provider.prefetch(runtime)
        assert "entries" in result
        assert len(result["entries"]) == 5
        assert result["user_id"] == "test_user"

    @pytest.mark.asyncio
    async def test_handle_tool_call_appends_entry(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = []

        await provider.handle_tool_call(runtime, "read_file", {"path": "/foo"}, "content")

        assert len(provider._entries) == 1
        assert provider._entries[0]["tool"] == "read_file"
        assert provider._entries[0]["observation"] == "Tool read_file called"

    @pytest.mark.asyncio
    async def test_handle_tool_call_ignores_non_tracked(self, provider, runtime):
        provider._user_id = "test_user"
        provider._entries = []

        await provider.handle_tool_call(runtime, "bash", {"command": "ls"}, "output")

        assert len(provider._entries) == 0  # bash is not tracked

    @pytest.mark.asyncio
    async def test_persistence_to_disk(self, provider, runtime, temp_dir):
        provider._user_id = "test_user"
        provider._entries = []

        await provider.handle_tool_call(runtime, "read_file", {"path": "/foo"}, "content")

        # Check file was written
        user_file = temp_dir / "user_model_test_user.jsonl"
        assert user_file.exists()
        with user_file.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "read_file"


class TestFactory:
    """Tests for create_user_model_provider factory."""

    def test_null_provider_by_default(self):
        provider = create_user_model_provider(None, config=MagicMock())
        assert isinstance(provider, NullUserModelProvider)

    def test_null_provider_explicit(self):
        provider = create_user_model_provider("null", config=MagicMock())
        assert isinstance(provider, NullUserModelProvider)

    def test_file_provider(self):
        with tempfile.TemporaryDirectory() as d:
            provider = create_user_model_provider("file", config=MagicMock(), storage_path=Path(d))
            assert isinstance(provider, FileUserModelProvider)

    def test_file_provider_requires_storage_path(self):
        with pytest.raises(ValueError, match="storage_path required"):
            create_user_model_provider("file", config=MagicMock())

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown user-model provider"):
            create_user_model_provider("unknown", config=MagicMock())


class TestABCContract:
    """Ensure all providers implement the full ABC."""

    def test_null_implements_abc(self):
        assert isinstance(NullUserModelProvider(), UserModelProvider)

    def test_file_implements_abc(self):
        with tempfile.TemporaryDirectory() as d:
            assert isinstance(FileUserModelProvider(storage_path=Path(d)), UserModelProvider)

    def test_abstract_methods_exist(self):
        methods = [
            "initialize",
            "system_prompt_block",
            "prefetch",
            "sync_turn",
            "handle_tool_call",
            "shutdown",
        ]
        for method in methods:
            assert hasattr(UserModelProvider, method)
            assert getattr(UserModelProvider, method).__isabstractmethod__


# Snapshot stability test - ensures null provider produces no prompt diff
class TestSnapshotStability:
    """Verify byte-identical prompts when null provider is used."""

    @pytest.mark.asyncio
    async def test_null_provider_adds_zero_bytes(self):
        """Null provider must not add any bytes to system prompt."""
        provider = NullUserModelProvider()
        runtime = MagicMock()
        runtime.context = {}

        block = provider.system_prompt_block(runtime)
        assert block is None, "Null provider must return None, not empty string"

    @pytest.mark.asyncio
    async def test_file_provider_deterministic_summary(self):
        """File provider summary must be deterministic for same entries."""
        with tempfile.TemporaryDirectory() as d:
            provider = FileUserModelProvider(storage_path=Path(d))
            provider._user_id = "test"
            provider._entries = [
                {"turn": 1, "observation": "obs", "reflection": "ref"},
                {"turn": 2, "observation": "obs2", "reflection": "ref2"},
            ]

            runtime = MagicMock()
            block1 = provider.system_prompt_block(runtime)
            block2 = provider.system_prompt_block(runtime)

            assert block1 == block2, "Summary must be deterministic"