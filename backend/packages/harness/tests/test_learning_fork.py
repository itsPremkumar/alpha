"""Tests for the learning fork middleware."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.runtime import Runtime

from alpha.agents.middlewares.learning_fork_middleware import (
    _WHITELISTED_TOOL_NAMES,
    LearningForkMiddleware,
    LearningForkMiddlewareState,
    _build_digest,
    build_learning_fork_middleware,
)
from alpha.config.learning_fork_config import LearningForkConfig


class TestBuildDigest:
    """Tests for _build_digest helper."""

    def test_empty_messages(self):
        assert _build_digest([], 8000) == ""

    def test_single_message(self):
        msg = MagicMock()
        msg.type = "human"
        msg.content = "Hello"
        result = _build_digest([msg], 8000)
        assert "[human] Hello" in result

    def test_multiple_messages_newest_first(self):
        msg1 = MagicMock()
        msg1.type = "human"
        msg1.content = "First"
        msg2 = MagicMock()
        msg2.type = "ai"
        msg2.content = "Second"
        result = _build_digest([msg1, msg2], 8000)
        # Newest (msg2) should appear last in the output
        assert result.index("[human] First") < result.index("[ai] Second")

    def test_cap_at_max_chars(self):
        msg = MagicMock()
        msg.type = "human"
        msg.content = "x" * 10000
        result = _build_digest([msg], 1000)
        assert len(result) <= 1000

    def test_multimodal_content(self):
        msg = MagicMock()
        msg.type = "human"
        msg.content = [
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        result = _build_digest([msg], 8000)
        assert "[human] Hello" in result


class TestLearningForkConfig:
    """Tests for LearningForkConfig."""

    def test_defaults(self):
        cfg = LearningForkConfig()
        assert cfg.enabled is False
        assert cfg.model_name is None
        assert cfg.max_proposals_per_run == 3
        assert cfg.digest_chars == 8000

    def test_enabled_config(self):
        cfg = LearningForkConfig(enabled=True, model_name="test-model", max_proposals_per_run=5)
        assert cfg.enabled is True
        assert cfg.model_name == "test-model"
        assert cfg.max_proposals_per_run == 5

    def test_bounds(self):
        with pytest.raises(ValueError):
            LearningForkConfig(max_proposals_per_run=11)
        with pytest.raises(ValueError):
            LearningForkConfig(max_proposals_per_run=-1)
        with pytest.raises(ValueError):
            LearningForkConfig(digest_chars=500)
        with pytest.raises(ValueError):
            LearningForkConfig(digest_chars=40000)


class TestLearningForkMiddleware:
    """Tests for LearningForkMiddleware."""

    @pytest.fixture
    def config(self):
        return LearningForkConfig(enabled=True, max_proposals_per_run=2, digest_chars=1000)

    @pytest.fixture
    def middleware(self, config):
        return LearningForkMiddleware(learning_fork_config=config)

    @pytest.fixture
    def state(self):
        return LearningForkMiddlewareState()

    @pytest.fixture
    def runtime(self):
        rt = MagicMock(spec=Runtime)
        rt.context = {"thread_id": "test-thread", "model_name": "test-model"}
        return rt

    @pytest.mark.asyncio
    async def test_disabled_config_returns_none(self, state, runtime):
        middleware = LearningForkMiddleware(learning_fork_config=LearningForkConfig(enabled=False))
        result = await middleware.after_agent(state, runtime)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_thread_id_returns_none(self, config, state, runtime):
        runtime.context = {}
        with patch("langgraph.config.get_config", return_value={"configurable": {}}):
            result = await middleware.after_agent(state, runtime)
            assert result is None

    @pytest.mark.asyncio
    async def test_no_messages_returns_none(self, config, state, runtime):
        state["messages"] = []
        result = await middleware.after_agent(state, runtime)
        assert result is None

    @pytest.mark.asyncio
    async def test_whitelisted_tools_only(self, middleware):
        assert "add_memory" in _WHITELISTED_TOOL_NAMES
        assert "recall_memory" in _WHITELISTED_TOOL_NAMES
        assert "propose_skill" in _WHITELISTED_TOOL_NAMES
        assert "bash" not in _WHITELISTED_TOOL_NAMES
        assert "str_replace" not in _WHITELISTED_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_release_policy_parameters(self, middleware):
        params = middleware.release_policy_parameters()
        assert params["learning_fork_enabled"] is True
        assert params["learning_fork_max_proposals"] == 2
        assert params["learning_fork_digest_chars"] == 1000


class TestLearningForkIntegration:
    """Integration-style tests with mocked dependencies."""

    @pytest.fixture
    def config(self):
        return LearningForkConfig(enabled=True, max_proposals_per_run=2, digest_chars=1000)

    @pytest.fixture
    def state(self):
        state = LearningForkMiddlewareState()
        state["messages"] = [
            MagicMock(type="human", content="Hello"),
            MagicMock(type="ai", content="Hi there!"),
        ]
        return state

    @pytest.fixture
    def runtime(self):
        rt = MagicMock(spec=Runtime)
        rt.context = {"thread_id": "test-thread", "model_name": "test-model"}
        return rt

    @pytest.mark.asyncio
    async def test_fork_invokes_model_and_tools(self, config, state, runtime):
        """Test that the fork calls the model and executes whitelisted tools."""
        middleware = LearningForkMiddleware(learning_fork_config=config)

        # Mock the model creation and invocation
        mock_model = AsyncMock()
        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "add_memory", "args": {"key": "value"}, "id": "call_1"},
            {"name": "propose_skill", "args": {"name": "test-skill", "description": "Test", "content": "..."}, "id": "call_2"},
        ]
        mock_model.ainvoke = AsyncMock(return_value=mock_response)

        with patch("alpha.agents.middlewares.learning_fork_middleware.create_chat_model", return_value=mock_model):
            with patch("alpha.agents.middlewares.learning_fork_middleware.get_memory_manager") as mock_get_manager:
                with patch("alpha.agents.middlewares.learning_fork_middleware.get_skill_proposal_store") as mock_get_store:
                    mock_manager = AsyncMock()
                    mock_manager.add = AsyncMock()
                    mock_get_manager.return_value = mock_manager

                    mock_store = AsyncMock()
                    mock_store.propose = AsyncMock()
                    mock_get_store.return_value = mock_store

                    result = await middleware.after_agent(state, runtime)

        assert result is None
        mock_model.ainvoke.assert_called_once()
        mock_manager.add.assert_called_once()
        mock_store.propose.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_proposals_enforced(self, config, state, runtime):
        """Test that max_proposals_per_run limit is enforced."""
        config.max_proposals_per_run = 1
        middleware = LearningForkMiddleware(learning_fork_config=config)

        mock_model = AsyncMock()
        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "add_memory", "args": {}, "id": "call_1"},
            {"name": "propose_skill", "args": {"name": "s1", "description": "d", "content": "c"}, "id": "call_2"},
            {"name": "propose_skill", "args": {"name": "s2", "description": "d", "content": "c"}, "id": "call_3"},
        ]
        mock_model.ainvoke = AsyncMock(return_value=mock_response)

        with patch("alpha.agents.middlewares.learning_fork_middleware.create_chat_model", return_value=mock_model):
            with patch("alpha.agents.middlewares.learning_fork_middleware.get_memory_manager") as mock_get_manager:
                with patch("alpha.agents.middlewares.learning_fork_middleware.get_skill_proposal_store") as mock_get_store:
                    mock_manager = AsyncMock()
                    mock_manager.add = AsyncMock()
                    mock_get_manager.return_value = mock_manager

                    mock_store = AsyncMock()
                    mock_store.propose = AsyncMock()
                    mock_get_store.return_value = mock_store

                    await middleware.after_agent(state, runtime)

        # Only 1 proposal should be made due to max_proposals_per_run=1
        total_calls = mock_manager.add.call_count + mock_store.propose.call_count
        assert total_calls == 1

    @pytest.mark.asyncio
    async def test_non_whitelisted_tool_skipped(self, config, state, runtime):
        """Test that non-whitelisted tool calls are skipped with warning."""
        middleware = LearningForkMiddleware(learning_fork_config=config)

        mock_model = AsyncMock()
        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "bash", "args": {"command": "ls"}, "id": "call_1"},
        ]
        mock_model.ainvoke = AsyncMock(return_value=mock_response)

        with patch("alpha.agents.middlewares.learning_fork_middleware.create_chat_model", return_value=mock_model):
            with patch("alpha.agents.middlewares.learning_fork_middleware.logger") as mock_logger:
                await middleware.after_agent(state, runtime)
                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_model_failure_isolated(self, config, state, runtime):
        """Test that model failures don't crash the primary run."""
        middleware = LearningForkMiddleware(learning_fork_config=config)

        with patch("alpha.agents.middlewares.learning_fork_middleware.create_chat_model", side_effect=Exception("Model failed")):
            with patch("alpha.agents.middlewares.learning_fork_middleware.logger") as mock_logger:
                result = await middleware.after_agent(state, runtime)
                assert result is None
                mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_tool_failure_isolated(self, config, state, runtime):
        """Test that tool failures in the fork don't crash the primary run."""
        middleware = LearningForkMiddleware(learning_fork_config=config)

        mock_model = AsyncMock()
        mock_response = MagicMock()
        mock_response.tool_calls = [
            {"name": "add_memory", "args": {}, "id": "call_1"},
        ]
        mock_model.ainvoke = AsyncMock(return_value=mock_response)

        with patch("alpha.agents.middlewares.learning_fork_middleware.create_chat_model", return_value=mock_model):
            with patch("alpha.agents.middlewares.learning_fork_middleware.get_memory_manager") as mock_get_manager:
                mock_manager = AsyncMock()
                mock_manager.add = AsyncMock(side_effect=Exception("Tool failed"))
                mock_get_manager.return_value = mock_manager

                with patch("alpha.agents.middlewares.learning_fork_middleware.logger") as mock_logger:
                    result = await middleware.after_agent(state, runtime)
                    assert result is None
                    mock_logger.warning.assert_called()


class TestFactory:
    """Test the factory function."""

    def test_build_learning_fork_middleware(self):
        mw = build_learning_fork_middleware()
        assert isinstance(mw, LearningForkMiddleware)
        assert mw._config.enabled is False  # Default config is disabled

    def test_build_with_custom_config(self):
        cfg = LearningForkConfig(enabled=True)
        mw = build_learning_fork_middleware(learning_fork_config=cfg)
        assert mw._config.enabled is True