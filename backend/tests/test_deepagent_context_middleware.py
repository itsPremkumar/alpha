"""Deep-agent working-plane projection into model requests.

The invariant these pin is the one `ViewImageMiddleware` already established for
its base64 payload: **a middleware that injects a hidden message owns it across
checkpoints**. A run interrupted after the payload reached state but before the
sweep removed it would otherwise resend a stale manifest for the life of the
thread, so both the reserved id prefix and the server-owned marker are required
before a message is treated as this middleware's own.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from alpha.agents.middlewares.deepagent_context_middleware import (
    DEEPAGENT_CONTEXT_MARKER,
    DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX,
    DeepAgentContextMiddleware,
)


class _Request:
    def __init__(self, messages, state=None):
        self.messages = list(messages)
        self.state = state if state is not None else {}


class _Handler:
    def __init__(self):
        self.seen = None

    def __call__(self, request):
        self.seen = list(request.messages)
        return "ok"


def _config(enabled: bool = True, inject_index: bool = True):
    return type("Cfg", (), {"deepagent": type("D", (), {"enabled": enabled, "inject_index": inject_index})()})()


class TestProjection:
    def test_an_empty_plane_injects_nothing(self):
        # An empty plane must produce byte-identical absence, not an empty
        # wrapper — otherwise every request grows a useless block.
        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": []})
        middleware.wrap_model_call(request, _Handler())
        assert request.messages[0].content == "hi"
        assert len(request.messages) == 1

    def test_a_nonempty_plane_appends_exactly_one_manifest(self):
        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "body", "summary": "the plan"}]})
        middleware.wrap_model_call(request, _Handler())
        assert len(request.messages) == 2
        injected = request.messages[-1]
        assert injected.content.startswith("<deep_agent_workspace>")
        assert "/a.md" in injected.content
        assert "the plan" in injected.content
        assert "body" not in injected.content

    def test_the_payload_rides_the_untrusted_channel(self):
        # The plane is model-authored, so it must never reach the model as a
        # system instruction — a summary the model wrote must not be able to
        # promote itself into authority text.
        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        assert not any(getattr(m, "type", None) == "system" for m in request.messages)
        assert isinstance(request.messages[-1], HumanMessage)

    def test_the_injected_message_is_stamped_and_marked(self):
        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        injected = request.messages[-1]
        assert injected.id == DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX
        assert injected.additional_kwargs.get(DEEPAGENT_CONTEXT_MARKER) is True
        assert injected.additional_kwargs.get("alpha_producer_kind") == "deepagent_context"

    def test_a_stranded_own_message_is_swept_before_a_fresh_one_is_built(self):
        middleware = DeepAgentContextMiddleware(app_config=_config())
        stranded = HumanMessage(
            content="<deep_agent_workspace>stale manifest</deep_agent_workspace>",
            id=DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX,
            additional_kwargs={DEEPAGENT_CONTEXT_MARKER: True},
        )
        request = _Request([HumanMessage("hi"), stranded], {"working_files": [{"path": "/a.md", "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        assert len(request.messages) == 2
        assert request.messages[-1].content != stranded.content

    def test_a_client_chosen_id_is_never_swept(self):
        # Only a message carrying BOTH the prefix and the marker is ours. A
        # client-supplied id that happens to share the prefix stays put.
        middleware = DeepAgentContextMiddleware(app_config=_config())
        foreign = HumanMessage(content="user text", id=DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX + "spoofed")
        request = _Request([foreign], {"working_files": []})
        middleware.wrap_model_call(request, _Handler())
        assert request.messages == [foreign]

    def test_a_disabled_feature_injects_nothing(self):
        middleware = DeepAgentContextMiddleware(app_config=_config(enabled=False))
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        assert len(request.messages) == 1

    def test_inject_index_false_keeps_the_tool_without_the_reminder(self):
        middleware = DeepAgentContextMiddleware(app_config=_config(inject_index=False))
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        assert len(request.messages) == 1

    def test_malformed_state_rows_are_ignored_not_raised(self):
        # A corrupt checkpoint must degrade to "no plane", never crash a run.
        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": ["not a dict", {"path": "/a.md"}, {"path": 7, "content": "x"}]})
        middleware.wrap_model_call(request, _Handler())
        assert len(request.messages) == 1

    def test_release_policy_declares_the_bounds(self):
        params = DeepAgentContextMiddleware(app_config=_config()).release_policy_parameters()
        assert params["enabled"] is True
        assert params["state_key"] == "working_files"
        assert isinstance(params["max_files"], int)
        assert "content" not in json.dumps(params)

    def test_async_path_projects_too(self):
        import asyncio

        middleware = DeepAgentContextMiddleware(app_config=_config())
        request = _Request([HumanMessage("hi")], {"working_files": [{"path": "/a.md", "content": "x"}]})

        class _AsyncHandler:
            async def __call__(self, req):
                return "ok"

        asyncio.run(middleware.awrap_model_call(request, _AsyncHandler()))
        assert len(request.messages) == 2
