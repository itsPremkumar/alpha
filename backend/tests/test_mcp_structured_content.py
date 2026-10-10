"""Opt-in MCP ``structuredContent`` -> model-visible text.

Why the feature exists
----------------------
An MCP server's *preferred* addressing data does not live in its text content. Cua
Driver's ``get_window_state`` returns the accessibility tree as Markdown and puts the
``element_token`` handles — plus the ``capture_id`` that admits pixel coordinates
against that exact capture — only in ``structuredContent``. LangChain keeps that as an
*artifact* on the ``ToolMessage``, which is a programmatic field no model is ever
shown. So with the default configuration the model's only route to those handles is
guessing pixel coordinates on a path the driver itself labels the weaker one.

What these tests pin
--------------------
1. **Default is byte-identical.** With the flag absent or false, the rendered content
   is exactly what it was before the feature existed. A presentation change nobody
   asked for is how an MCP integration quietly doubles a turn's context cost.
2. **Opt-in surfaces it, fenced and intact**, so a model can tell where the server's
   prose ended.
3. **The appended bytes are bounded** by the same per-result budget as every other
   server-supplied byte, and truncation is marked rather than silent.
4. **The artifact still carries the payload** when the flag is on — this augments the
   text, it does not replace the structured path.
5. **stdio only.** HTTP/SSE tools are built by the upstream adapter's converter, which
   keeps structured content as an artifact; setting the flag there logs a warning
   rather than appearing to work.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from alpha.config.extensions_config import ExtensionsConfig, McpServerConfig
from alpha.mcp import tools as mcp_tools
from alpha.mcp.tools import (
    STRUCTURED_CONTENT_CLOSE,
    STRUCTURED_CONTENT_OPEN,
    _convert_call_tool_result,
    _resolve_include_structured_content,
)


def _result(*, text: str = "tree rendered", structured: Any = None) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=False,
    )


def _texts(content: list[dict]) -> list[str]:
    return [block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text"]


class TestDefaultIsUnchanged:
    """The feature must be invisible unless an operator asks for it."""

    def test_absent_flag_leaves_the_result_byte_identical(self) -> None:
        content, artifact = _convert_call_tool_result(_result(structured={"element_token": "s1:7"}))

        assert _texts(content) == ["tree rendered"], "structuredContent must not appear in the text by default"
        assert artifact == {"structured_content": {"element_token": "s1:7"}}, "the artifact path is unchanged"

    def test_explicit_false_matches_the_default(self) -> None:
        content, artifact = _convert_call_tool_result(
            _result(structured={"element_token": "s1:7"}),
            include_structured_content=False,
        )

        assert _texts(content) == ["tree rendered"]
        assert artifact == {"structured_content": {"element_token": "s1:7"}}

    def test_resolver_defaults_to_off_for_every_server_shape(self) -> None:
        assert _resolve_include_structured_content(None) is False
        assert _resolve_include_structured_content(McpServerConfig(enabled=True)) is False


class TestOptIn:
    """The flag surfaces the payload without breaking the artifact path."""

    def test_flag_appends_a_fenced_block_carrying_the_payload(self) -> None:
        content, artifact = _convert_call_tool_result(
            _result(structured={"elements": [{"element_token": "s1:31", "role": "Button", "label": "Six"}]}),
            include_structured_content=True,
        )

        texts = _texts(content)
        assert texts[0] == "tree rendered", "the server's own text must still come first"
        assert len(texts) == 2, f"exactly one block is appended, got {len(texts)}"

        block = texts[1]
        assert block.startswith(STRUCTURED_CONTENT_OPEN)
        assert block.rstrip().endswith(STRUCTURED_CONTENT_CLOSE)
        payload = json.loads(block[len(STRUCTURED_CONTENT_OPEN) : -len(STRUCTURED_CONTENT_CLOSE)].strip())
        assert payload["elements"][0]["element_token"] == "s1:31", "the handle must survive intact"
        assert artifact == {"structured_content": {"elements": [{"element_token": "s1:31", "role": "Button", "label": "Six"}]}}, "the artifact still carries the payload; the text is additive"

    def test_no_structured_content_appends_nothing_even_when_asked(self) -> None:
        content, artifact = _convert_call_tool_result(_result(), include_structured_content=True)

        assert _texts(content) == ["tree rendered"], "a server that sends nothing structured must render nothing extra"
        assert artifact is None

    def test_unserialisable_payload_reports_itself_instead_of_raising(self) -> None:
        # A payload json.dumps cannot encode must not become a tool-call failure: the
        # action already happened, and raising here would report it as an error.
        class Unserialisable:
            def __repr__(self) -> str:
                raise RuntimeError("even repr refuses")

        content, artifact = _convert_call_tool_result(
            _result(structured={"weird": Unserialisable()}),
            include_structured_content=True,
        )

        texts = _texts(content)
        assert len(texts) == 2
        assert STRUCTURED_CONTENT_OPEN in texts[1]
        assert artifact is not None, "the artifact still holds the raw payload"

    def test_a_servers_own_text_is_never_replaced(self) -> None:
        content, _ = _convert_call_tool_result(
            _result(text="server prose that matters", structured={"k": "v"}),
            include_structured_content=True,
        )

        assert _texts(content)[0] == "server prose that matters"


class TestBudget:
    """Every byte from outside the trust boundary stays bounded."""

    def test_an_oversized_payload_is_truncated_and_marked(self, caplog: pytest.LogCaptureFixture) -> None:
        payload = {"elements": [{"element_token": f"s1:{index}", "label": "x" * 200} for index in range(5000)]}

        with caplog.at_level(logging.WARNING):
            content, _ = _convert_call_tool_result(_result(structured=payload), include_structured_content=True)

        texts = _texts(content)
        assert len(texts) == 2
        block = texts[1]
        # The per-result budget is finite, so a 5000-element payload cannot pass whole.
        assert len(block) < len(json.dumps(payload)), "an oversized payload must be cut, not passed through"
        assert "truncated" in block.lower() or block.rstrip().endswith(STRUCTURED_CONTENT_CLOSE)
        assert any("Truncated MCP tool result text" in record.message for record in caplog.records), "truncation must be logged, not silent"


class TestConfigSurface:
    """The flag is a per-server presentation opt-in, shaped like tool_name_prefix."""

    def test_server_config_carries_the_flag_with_a_false_default(self) -> None:
        assert McpServerConfig(enabled=True).include_structured_content is False

    def test_resolver_reads_the_flag(self) -> None:
        assert _resolve_include_structured_content(McpServerConfig(enabled=True, include_structured_content=True)) is True

    def test_it_survives_a_json_config_round_trip(self, tmp_path: Any) -> None:
        path = tmp_path / "extensions_config.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "cua-driver": {
                            "enabled": True,
                            "type": "stdio",
                            "command": "cua-driver",
                            "args": ["mcp"],
                            "include_structured_content": True,
                        },
                        "other": {"enabled": True, "type": "stdio", "command": "other"},
                    }
                }
            ),
            encoding="utf-8",
        )

        config = ExtensionsConfig.from_file(path)

        assert config.mcp_servers["cua-driver"].include_structured_content is True
        assert config.mcp_servers["other"].include_structured_content is False, "one server opting in must not opt in every other server"


class TestStdioOnly:
    """HTTP/SSE tools go through the upstream converter, which only keeps an artifact."""

    @pytest.mark.asyncio
    async def test_non_stdio_server_warns_instead_of_silently_doing_nothing(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from unittest import mock

        # get_mcp_tools imports these inside the function body, so the patch targets
        # the upstream modules rather than this one.
        with (
            mock.patch("langchain_mcp_adapters.client.MultiServerMCPClient") as mock_client_cls,
            mock.patch("langchain_mcp_adapters.tools.load_mcp_tools") as mock_load,
            mock.patch("alpha.mcp.tools.ExtensionsConfig.from_file") as mock_from_file,
            caplog.at_level(logging.WARNING),
        ):
            extensions_config = ExtensionsConfig(
                mcpServers={
                    "remote": McpServerConfig(
                        enabled=True,
                        type="http",
                        url="http://localhost:9/mcp",
                        include_structured_content=True,
                    )
                }
            )
            mock_from_file.return_value = extensions_config

            tool = mock.Mock()
            tool.name = "remote_some_tool"
            tool.description = "desc"
            tool.args_schema = None
            tool.metadata = None
            mock_load.return_value = [tool]

            async def _get_tools(**kwargs: Any) -> list[Any]:
                return [tool]

            mock_client_cls.return_value.get_tools = _get_tools

            await mcp_tools.get_mcp_tools()

        assert any("include_structured_content" in record.message and "not stdio" in record.message for record in caplog.records), "an operator who sets this on an HTTP/SSE server must be told it does nothing, not left to assume it works"


def test_structured_markers_are_distinct() -> None:
    """A marker that cannot be closed would make the block ambiguous to a reader."""

    assert STRUCTURED_CONTENT_OPEN != STRUCTURED_CONTENT_CLOSE
    assert not STRUCTURED_CONTENT_OPEN.endswith(STRUCTURED_CONTENT_CLOSE)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(asyncio.sleep(0))
