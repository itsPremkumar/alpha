"""Tests for automatic thread title generation.

TitleConfig validation plus the integration-level coverage: a real langgraph
runtime where the middleware hook fires inside a compiled graph and the title
round-trips through a checkpointer, and concurrent generation. Unit behavior
(trigger logic, generation, hook delegation, LLM-failure fallback) lives in
``test_title_middleware_core_logic.py``.
"""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from alpha.agents.middlewares import title_middleware as title_middleware_module
from alpha.agents.middlewares.title_middleware import TitleMiddleware
from alpha.config.title_config import TitleConfig, get_title_config, set_title_config


class TestTitleConfig:
    """Tests for TitleConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = TitleConfig()
        assert config.enabled is True
        assert config.max_words == 6
        assert config.max_chars == 60
        assert config.model_name is None

    def test_custom_config(self):
        """Test custom configuration."""
        config = TitleConfig(
            enabled=False,
            max_words=10,
            max_chars=100,
            model_name="gpt-4",
        )
        assert config.enabled is False
        assert config.max_words == 10
        assert config.max_chars == 100
        assert config.model_name == "gpt-4"

    def test_config_validation(self):
        """Test configuration validation."""
        # max_words should be between 1 and 20
        with pytest.raises(ValueError):
            TitleConfig(max_words=0)
        with pytest.raises(ValueError):
            TitleConfig(max_words=21)

        # max_chars should be between 10 and 200
        with pytest.raises(ValueError):
            TitleConfig(max_chars=5)
        with pytest.raises(ValueError):
            TitleConfig(max_chars=201)

    def test_get_set_config(self):
        """Test global config getter and setter."""
        original_config = get_title_config()

        # Set new config
        new_config = TitleConfig(enabled=False, max_words=10)
        set_title_config(new_config)

        # Verify it was set
        assert get_title_config().enabled is False
        assert get_title_config().max_words == 10

        # Restore original config
        set_title_config(original_config)


class TestTitleMiddleware:
    """Tests for TitleMiddleware."""

    def test_middleware_initialization(self):
        """Test middleware can be initialized."""
        middleware = TitleMiddleware()
        assert middleware is not None
        assert middleware.state_schema is not None

    @pytest.mark.parametrize(
        "use_async",
        [False, True],
        ids=["invoke", "ainvoke"],
    )
    def test_real_graph_hook_persists_title_to_checkpointer(self, use_async):
        """The hook fires inside a real compiled graph and the title survives
        in the checkpointer, not just in the returned dict.

        A second graph instance bound to the same ``InMemorySaver`` reads the
        title back, proving the persistence path rather than in-memory state.
        """
        original = TitleConfig(**get_title_config().model_dump())
        try:
            set_title_config(TitleConfig(enabled=True, model_name=None, max_chars=60))
            from langchain.agents import create_agent
            from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
            from langgraph.checkpoint.memory import InMemorySaver

            from alpha.agents.thread_state import ThreadState

            class _FakeModel(FakeMessagesListChatModel):
                def bind_tools(self, tools, **kwargs):  # type: ignore[override]
                    return self

            prompt_text = "Summarize this repository for me"
            checkpointer = InMemorySaver()
            graph = create_agent(
                model=_FakeModel(responses=[AIMessage(content="Done. It is summarized.")]),
                tools=[],
                middleware=[TitleMiddleware()],
                state_schema=ThreadState,
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": f"title-checkpoint-{use_async}"}}

            if use_async:
                result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content=prompt_text)]}, config=config))
            else:
                result = graph.invoke({"messages": [HumanMessage(content=prompt_text)]}, config=config)

            # Hook fired inside the real runtime and merged its state update.
            assert result["title"] == prompt_text

            # Persistence: a fresh graph bound to the same checkpointer sees it.
            rebound = create_agent(
                model=_FakeModel(responses=[]),
                tools=[],
                middleware=[TitleMiddleware()],
                state_schema=ThreadState,
                checkpointer=checkpointer,
            )
            assert rebound.get_state(config).values.get("title") == prompt_text
        finally:
            set_title_config(original)

    def test_concurrent_sync_title_generation_stays_isolated(self):
        """Parallel first-turn runs share this middleware instance; no cross-talk."""
        original = TitleConfig(**get_title_config().model_dump())
        try:
            set_title_config(TitleConfig(enabled=True, model_name=None, max_chars=60))
            middleware = TitleMiddleware()
            prompts = [f"并发问题 {i}" for i in range(24)]

            def _state(prompt: str) -> dict:
                return {
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": "好的"},
                    ]
                }

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda p: middleware._generate_title_result(_state(p)), prompts))

            assert [r["title"] if r else None for r in results] == prompts
        finally:
            set_title_config(original)

    def test_concurrent_async_title_generation_binds_each_result(self, monkeypatch):
        """Concurrent LLM titles interleave safely: job N's answer returns to job N.

        Sleeps are scrambled so completion order differs from submission order,
        and each fake-model answer is derived from the prompt it received, so
        any cross-task mis-binding fails the mapping.
        """
        original = TitleConfig(**get_title_config().model_dump())
        try:
            set_title_config(TitleConfig(enabled=True, model_name="title-model", max_chars=60))
            middleware = TitleMiddleware()

            async def _ainvoke(prompt, config=None, **kwargs):
                match = re.search(r"job-(\d+)", prompt)
                assert match is not None, "title prompt must carry the job marker"
                index = int(match.group(1))
                await asyncio.sleep(0.001 * (index % 5 + 1))
                return AIMessage(content=f"Title job-{index}")

            model = MagicMock()
            model.ainvoke = _ainvoke
            create_model_mock = MagicMock(return_value=model)
            monkeypatch.setattr(title_middleware_module, "create_chat_model", create_model_mock)

            states = [
                {
                    "messages": [
                        HumanMessage(content=f"please handle job-{i} today"),
                        AIMessage(content="ok"),
                    ]
                }
                for i in range(10)
            ]

            async def _run_all():
                return await asyncio.gather(*(middleware._agenerate_title_result(state) for state in states))

            results = asyncio.run(_run_all())

            assert [r["title"] for r in results] == [f"Title job-{i}" for i in range(10)]
            assert create_model_mock.call_count == 10
        finally:
            set_title_config(original)


# Coverage map for the former TODO list in this file (each claim verified):
# - trigger logic / generation / after_model hooks with a mock Runtime:
#   test_title_middleware_core_logic.py
# - real LangGraph runtime + title persistence with a checkpointer:
#   test_real_graph_hook_persists_title_to_checkpointer above (rebound-graph
#   read), and test_runtime_lifecycle_e2e.py (_wait_for_thread_title /
#   _wait_for_search_title assert gateway-visible persistence end to end)
# - fallback behavior when the LLM fails: test_title_middleware_core_logic.py
#   (test_generate_title_fallback_for_long_message et al.)
# - concurrent title generation: the two concurrency tests above
