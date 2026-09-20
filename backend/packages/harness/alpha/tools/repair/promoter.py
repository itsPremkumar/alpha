"""Promotes plain-text model emissions into valid native tool call executions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from alpha.tools.repair.normalizer import repair_json_payload


@dataclass
class RepairedToolCall:
    """A promoted tool call reconstructed from plain-text model output."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    call_id: str = ""


class ToolCallPromoter:
    """Detects embedded tool invocations in plain-text LLM responses and promotes them."""

    _TOOL_TAG_PATTERN = re.compile(
        r"<tool_call>\s*(.*?)\s*</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )
    _ACTION_PATTERN = re.compile(
        r"Action:\s*([a-zA-Z0-9_-]+)\s*\nAction Input:\s*(\{.*?\}|\[.*?\]|.+)",
        re.DOTALL | re.IGNORECASE,
    )

    @classmethod
    def detect_and_promote(
        cls,
        text: str,
        available_tools: Sequence[str] | None = None,
    ) -> list[RepairedToolCall]:
        """Detect and promote any plain-text tool calls found in the message."""
        results: list[RepairedToolCall] = []
        valid_names = set(m.lower() for m in (available_tools or []))

        # 1. Check for XML-style <tool_call>...</tool_call>
        for match in cls._TOOL_TAG_PATTERN.finditer(text):
            payload_str = match.group(1).strip()
            parsed = repair_json_payload(payload_str)
            if isinstance(parsed, dict) and "name" in parsed:
                tool_name = str(parsed["name"]).lower().strip()
                if not valid_names or tool_name in valid_names:
                    args = parsed.get("arguments", parsed.get("parameters", {}))
                    if not isinstance(args, dict):
                        args = {"input": args}
                    results.append(
                        RepairedToolCall(
                            name=tool_name,
                            arguments=args,
                            raw_text=match.group(0),
                        )
                    )

        # 2. Check for markdown code blocks containing JSON with "name"/"tool" & "arguments"
        codeblock_pattern = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
        for match in codeblock_pattern.finditer(text):
            parsed = repair_json_payload(match.group(1))
            if isinstance(parsed, dict):
                name = parsed.get("name") or parsed.get("tool") or parsed.get("function")
                if name and isinstance(name, str):
                    tool_name = name.lower().strip()
                    if not valid_names or tool_name in valid_names:
                        args = parsed.get("arguments", parsed.get("parameters", {}))
                        if not isinstance(args, dict):
                            args = {"input": args}
                        results.append(
                            RepairedToolCall(
                                name=tool_name,
                                arguments=args,
                                raw_text=match.group(0),
                            )
                        )

        # 3. Check for Action / Action Input pattern
        for match in cls._ACTION_PATTERN.finditer(text):
            tool_name = match.group(1).strip().lower()
            if not valid_names or tool_name in valid_names:
                raw_input = match.group(2).strip()
                parsed_args = repair_json_payload(raw_input)
                if isinstance(parsed_args, dict):
                    args = parsed_args
                else:
                    args = {"command": raw_input} if tool_name in ("bash", "shell") else {"input": raw_input}
                results.append(
                    RepairedToolCall(
                        name=tool_name,
                        arguments=args,
                        raw_text=match.group(0),
                    )
                )

        return results
