"""Robust JSON and tool call payload repair utilities."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MARKDOWN_CODEBLOCK_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_TRAILING_COMMA_PATTERN = re.compile(r",\s*([}\]])")
_UNQUOTED_KEY_PATTERN = re.compile(r'([{\s,])([a-zA-Z_][a-zA-Z0-9_-]*)\s*:')
_PYTHON_LITERALS = [
    (re.compile(r"\bTrue\b"), "true"),
    (re.compile(r"\bFalse\b"), "false"),
    (re.compile(r"\bNone\b"), "null"),
]


def repair_json_payload(raw: str) -> dict[str, Any] | list[Any] | None:
    """Attempt multi-stage healing of corrupted or malformed model JSON payloads."""
    text = raw.strip()
    if not text:
        return None

    # Step 1: Extract from markdown code blocks if wrapped
    match = _MARKDOWN_CODEBLOCK_PATTERN.search(text)
    if match:
        text = match.group(1).strip()

    # Step 2: Direct standard parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Step 3: Replace Python literals
    repaired = text
    for pat, rep in _PYTHON_LITERALS:
        repaired = pat.sub(rep, repaired)

    # Step 4: Fix trailing commas
    repaired = _TRAILING_COMMA_PATTERN.sub(r"\1", repaired)

    # Step 5: Fix unquoted keys
    repaired = _UNQUOTED_KEY_PATTERN.sub(r'\1"\2":', repaired)

    try:
        return json.loads(repaired)
    except Exception:
        pass

    # Step 6: Single-quote normalization
    # If the text uses single quotes for strings, replace with double quotes carefully
    try:
        # ast.literal_eval handles Python dictionary syntax safely
        evaluated = ast.literal_eval(text)
        if isinstance(evaluated, (dict, list)):
            return evaluated
    except Exception:
        pass

    # Step 7: Substring JSON isolation: find first '{' and last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        sub = text[start : end + 1]
        for pat, rep in _PYTHON_LITERALS:
            sub = pat.sub(rep, sub)
        sub = _TRAILING_COMMA_PATTERN.sub(r"\1", sub)
        sub = _UNQUOTED_KEY_PATTERN.sub(r'\1"\2":', sub)
        try:
            return json.loads(sub)
        except Exception:
            try:
                ev = ast.literal_eval(sub)
                if isinstance(ev, (dict, list)):
                    return ev
            except Exception:
                pass

    return None


class ToolCallNormalizer:
    """Repairs and normalizes tool call inputs and arguments."""

    @staticmethod
    def normalize_arguments(args: Any) -> dict[str, Any]:
        """Ensure tool arguments are a valid dictionary."""
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            res = repair_json_payload(args)
            if isinstance(res, dict):
                return res
            return {"input": args}
        return {"raw": str(args)}
