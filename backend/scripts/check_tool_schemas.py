"""Verify every registered tool generates a valid JSON schema.

The `harness_refine` failure mode: a tool whose signature contains a type pydantic
cannot express (e.g. `ToolRuntime | None`, whose stream_writer is a Callable) makes
the WHOLE tool list unusable for the LLM -- every agent turn fails with
"Failed to generate a JSON schema for '<tool>'".

This script imports BUILTIN_TOOLS and forces schema generation for each, reporting
every failure instead of only the first.
"""

from __future__ import annotations

import sys
import traceback

from alpha.tools.tools import BUILTIN_TOOLS

failures: list[tuple[str, str]] = []
ok = 0

for tool in BUILTIN_TOOLS:
    name = getattr(tool, "name", repr(tool))
    try:
        # .tool_call_schema triggers the same pydantic JSON-schema generation the
        # model binding uses. Any unsupported type raises here.
        schema = tool.tool_call_schema
        schema.model_json_schema()
        ok += 1
    except Exception as exc:  # noqa: BLE001 - report every failure
        failures.append((name, f"{type(exc).__name__}: {exc}"))

print(f"tools checked: {len(BUILTIN_TOOLS)}  ok: {ok}  failed: {len(failures)}")
for name, err in failures:
    print(f"  FAIL {name}: {err}")

if failures:
    print("\nThese tools must use a bare `runtime: Runtime` parameter (no union, no default).")
    sys.exit(1)
print("ALL TOOL SCHEMAS OK")
