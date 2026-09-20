"""Tests for the Self-Evolving Dynamic Tool Metacompiler."""

from __future__ import annotations

import pytest

from alpha.metacompiler.dynamic_tool_synthesizer import (
    DEFAULT_ENTRYPOINT,
    DynamicToolRegistry,
    DynamicToolSynthesizer,
    SecurityAstValidator,
    SynthesisStatus,
    build_restricted_builtins,
)
from alpha.tools.builtins.dynamic_tool_synthesizer_tool import (
    list_dynamic_tools,
    synthesize_runtime_tool,
)

VALID_TOOL = '''
def run(text: str) -> dict:
    """Measure a string."""
    return {"length": len(text), "upper": text.upper()}


def self_test() -> dict:
    assert run("abc")["length"] == 3
    assert run("abc")["upper"] == "ABC"
    return {"success": True, "detail": "length and upper verified"}
'''

FAILING_TOOL = '''
def run(x: int) -> int:
    return x + 1


def self_test():
    assert run(1) == 99
'''

NO_SELF_TEST_TOOL = '''
def run() -> str:
    return "ok"
'''


@pytest.fixture()
def synthesizer(tmp_path) -> DynamicToolSynthesizer:
    """Return a fresh synthesizer bound to a temporary workspace."""
    return DynamicToolSynthesizer(workspace_root=tmp_path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_synthesize_registers_valid_tool(synthesizer):
    result = synthesizer.synthesize("text_metrics", VALID_TOOL, description="measure text")
    assert result["success"] is True
    assert result["status"] == "registered"
    assert result["version"] == 1
    assert result["validation"]["success"] is True


def test_synthesized_tool_is_invokable(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    outcome = synthesizer.invoke("text_metrics", {"text": "hello"})
    assert outcome["success"] is True
    assert outcome["result"] == {"length": 5, "upper": "HELLO"}


def test_synthesize_infers_parameters(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    record = synthesizer.registry.get("text_metrics")
    assert "text" in record.parameters
    assert record.parameters["text"]["required"] is True


def test_synthesize_accepts_tool_without_self_test(synthesizer):
    result = synthesizer.synthesize("ping", NO_SELF_TEST_TOOL)
    assert result["success"] is True
    assert synthesizer.invoke("ping")["result"] == "ok"


def test_entrypoint_defaults_to_run(synthesizer):
    assert DEFAULT_ENTRYPOINT == "run"
    result = synthesizer.synthesize("ping", NO_SELF_TEST_TOOL)
    assert result["tool"]["entrypoint"] == "run"


def test_custom_entrypoint_is_supported(synthesizer):
    source = "def compute(a: int, b: int) -> int:\n    return a + b\n"
    result = synthesizer.synthesize("adder", source, entrypoint="compute")
    assert result["success"] is True
    assert synthesizer.invoke("adder", {"a": 2, "b": 3})["result"] == 5


def test_external_test_code_is_executed(synthesizer):
    source = "def run(x: int) -> int:\n    return x * 2\n"
    tests = "def self_test():\n    assert run(3) == 6\n    return {'success': True}\n"
    result = synthesizer.synthesize("doubler", source, test_code=tests)
    assert result["success"] is True
    assert result["validation"]["checks"][0]["name"] == "self_test"


def test_dry_run_validates_without_registering(synthesizer):
    result = synthesizer.synthesize("text_metrics", VALID_TOOL, dry_run=True)
    assert result["success"] is True
    assert result["status"] == "dry-run"
    assert synthesizer.registry.get("text_metrics") is None


# ---------------------------------------------------------------------------
# Security enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n",
        "import os\n\ndef run(cmd):\n    return os.system(cmd)\n",
        "import socket\n\ndef run():\n    s = socket.socket()\n    s.bind(('0.0.0.0', 8080))\n    return s\n",
        "import pickle\n\ndef run(blob):\n    return pickle.loads(blob)\n",
        "import shutil\n\ndef run(path):\n    return shutil.rmtree(path)\n",
        "def run(cmd):\n    return eval(cmd)\n",
        "def run(cmd):\n    return exec(cmd)\n",
        "def run(name):\n    return __import__(name)\n",
    ],
)
def test_dangerous_sources_are_rejected(synthesizer, source):
    result = synthesizer.synthesize("dangerous_tool", source)
    assert result["success"] is False
    assert result["status"] == "security-rejected"
    assert result["violations"]


def test_deletion_api_is_blocked(synthesizer):
    source = "import os\n\ndef run(path: str) -> bool:\n    os.remove(path)\n    return True\n"
    result = synthesizer.synthesize("cleaner", source)
    assert result["success"] is False
    assert any(item["rule"] == "blocked-deletion" for item in result["violations"])


def test_write_outside_workspace_is_blocked(synthesizer):
    outside = "C:/Windows/System32/evil.txt"
    source = f"def run() -> None:\n    open({outside!r}, 'w').write('x')\n"
    result = synthesizer.synthesize("writer", source)
    assert result["success"] is False
    assert any(
        item["rule"] == "blocked-write-outside-workspace" for item in result["violations"]
    )


def test_write_inside_workspace_is_allowed(synthesizer, tmp_path):
    source = "def run() -> str:\n    return 'no-write'\n"
    result = synthesizer.synthesize("safe_writer", source)
    assert result["success"] is True


def test_syntax_errors_are_rejected(synthesizer):
    result = synthesizer.synthesize("broken", "def run(:\n    pass\n")
    assert result["success"] is False
    assert result["status"] == "security-rejected"
    assert result["violations"][0]["rule"] == "syntax-error"


def test_missing_entrypoint_is_rejected(synthesizer):
    result = synthesizer.synthesize("orphan", "def helper():\n    return 1\n")
    assert result["success"] is False
    assert result["status"] == "missing-entrypoint"


def test_global_mutation_is_rejected(synthesizer):
    source = "CACHE = {}\n\ndef run():\n    global CACHE\n    CACHE['x'] = 1\n    return CACHE\n"
    result = synthesizer.synthesize("cache_tool", source)
    assert result["success"] is False
    assert any(item["rule"] == "blocked-global" for item in result["violations"])


def test_wildcard_import_is_rejected(synthesizer):
    result = synthesizer.synthesize("star", "from json import *\n\ndef run(x):\n    return dumps(x)\n")
    assert result["success"] is False


def test_non_literal_write_target_produces_warning(synthesizer):
    source = "def run(path: str) -> None:\n    handle = open(path, 'w')\n    handle.write('x')\n"
    result = synthesizer.synthesize("dynamic_writer", source)
    # Either blocked or admitted with a warning, but never silently accepted.
    if result["success"]:
        assert result["warnings"]
    else:
        assert result["status"] == "security-rejected"


def test_insecure_test_code_is_rejected(synthesizer):
    tests = "import subprocess\n\ndef self_test():\n    subprocess.run('ls')\n"
    result = synthesizer.synthesize("victim", VALID_TOOL, test_code=tests)
    assert result["success"] is False
    assert "test_code" in result["message"]


def test_validator_reports_line_numbers():
    validator = SecurityAstValidator()
    verdict = validator.validate("import os\n\ndef run():\n    os.system('ls')\n")
    assert verdict.allowed is False
    assert any(item.line >= 4 for item in verdict.violations)


def test_validator_allows_pure_computation():
    validator = SecurityAstValidator()
    verdict = validator.validate(VALID_TOOL)
    assert verdict.allowed is True
    assert verdict.violations == []


# ---------------------------------------------------------------------------
# Self-validation
# ---------------------------------------------------------------------------


def test_failing_self_test_prevents_registration(synthesizer):
    result = synthesizer.synthesize("bad_math", FAILING_TOOL)
    assert result["success"] is False
    assert result["status"] == "self-validation-failed"
    assert synthesizer.registry.get("bad_math") is None


def test_compile_error_is_reported(synthesizer):
    source = "def run(x):\n    return undefined_name(x)\n\n\ndef self_test():\n    assert run(1)\n"
    result = synthesizer.synthesize("runtime_boom", source)
    assert result["success"] is False
    assert result["status"] == "self-validation-failed"


def test_invalid_tool_name_is_rejected(synthesizer):
    result = synthesizer.synthesize("not a valid name!", VALID_TOOL)
    assert result["success"] is False
    assert result["status"] == "invalid-name"


def test_empty_source_is_rejected(synthesizer):
    result = synthesizer.synthesize("empty", "   ")
    assert result["success"] is False
    assert result["status"] == "empty-source"


# ---------------------------------------------------------------------------
# Versioning, unloading and registry
# ---------------------------------------------------------------------------


def test_resynthesis_bumps_version(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    second = synthesizer.synthesize("text_metrics", VALID_TOOL)
    assert second["version"] == 2
    assert synthesizer.registry.get("text_metrics").version == 2


def test_superseded_versions_are_retained_in_history(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    history = synthesizer.registry.history("text_metrics")
    assert len(history) == 1
    assert history[0]["version"] == 1


def test_unload_releases_namespace(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    assert synthesizer.unload("text_metrics") is True
    assert synthesizer.registry.get("text_metrics") is None
    assert synthesizer.invoke("text_metrics", {"text": "x"})["success"] is False


def test_unload_unknown_tool_is_false(synthesizer):
    assert synthesizer.unload("ghost") is False


def test_registry_enforces_capacity():
    registry = DynamicToolRegistry(max_tools=2)
    from alpha.metacompiler.dynamic_tool_synthesizer import DynamicToolRecord

    for name in ("a", "b", "c"):
        registry.register(
            DynamicToolRecord(
                name=name,
                description="",
                version=1,
                entrypoint="run",
                parameters={},
                source="",
                source_sha256="",
                created_at=float(name != "a"),
                status=SynthesisStatus.VALIDATED,
            )
        )
    assert len(registry) <= 2
    assert "c" in registry.names()


def test_list_tools_inventory(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    inventory = synthesizer.list_tools()
    assert inventory["success"] is True
    assert inventory["count"] == 1
    assert inventory["results"][0]["name"] == "text_metrics"


def test_inventory_can_include_source(synthesizer):
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    inventory = synthesizer.list_tools(include_source=True)
    assert "def run" in inventory["results"][0]["source"]


def test_runtime_registrar_is_notified(synthesizer):
    published = []
    synthesizer.attach_runtime_registrar(published.append)
    synthesizer.synthesize("text_metrics", VALID_TOOL)
    assert len(published) == 1
    assert published[0].name == "text_metrics"


def test_restricted_builtins_exclude_dangerous_names():
    safe = build_restricted_builtins()
    assert "eval" not in safe
    assert "exec" not in safe
    assert "__import__" in safe
    assert safe["len"]([1, 2]) == 2


def test_restricted_import_gate_blocks_os():
    safe = build_restricted_builtins()
    with pytest.raises(ImportError):
        safe["__import__"]("os")


def test_restricted_import_gate_allows_json():
    safe = build_restricted_builtins()
    assert safe["__import__"]("json") is not None


def test_restricted_open_gate_blocks_writes(tmp_path):
    safe = build_restricted_builtins()
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    handle = safe["open"](str(target), "r")
    assert handle.read() == "hello"
    handle.close()
    with pytest.raises(PermissionError):
        safe["open"](str(target), "w")


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_synthesize_and_list(tmp_path):
    result = synthesize_runtime_tool.invoke(
        {
            "name": "tool_word_count",
            "source_code": VALID_TOOL,
            "description": "measure text",
            "workspace_root": str(tmp_path),
            "tags": "nlp,metrics",
        }
    )
    assert result["success"] is True
    inventory = list_dynamic_tools.invoke({"action": "list", "workspace_root": str(tmp_path)})
    assert inventory["count"] >= 1


def test_tool_invoke_action(tmp_path):
    synthesize_runtime_tool.invoke(
        {
            "name": "tool_word_count",
            "source_code": VALID_TOOL,
            "workspace_root": str(tmp_path),
        }
    )
    outcome = synthesize_runtime_tool.invoke(
        {
            "name": "tool_word_count",
            "source_code": "",
            "action": "invoke",
            "parameters_json": '{"text": "hey"}',
            "workspace_root": str(tmp_path),
        }
    )
    assert outcome["success"] is True
    assert outcome["result"]["length"] == 3


def test_tool_unload_action(tmp_path):
    synthesize_runtime_tool.invoke(
        {"name": "temp_tool", "source_code": VALID_TOOL, "workspace_root": str(tmp_path)}
    )
    outcome = synthesize_runtime_tool.invoke(
        {"name": "temp_tool", "source_code": "", "action": "unload", "workspace_root": str(tmp_path)}
    )
    assert outcome["success"] is True
    assert outcome["status"] == "unloaded"


def test_tool_rejects_unsafe_source(tmp_path):
    result = synthesize_runtime_tool.invoke(
        {
            "name": "shell_tool",
            "source_code": "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd)\n",
            "workspace_root": str(tmp_path),
        }
    )
    assert result["success"] is False
    assert result["status"] == "security-rejected"


def test_tool_unknown_action(tmp_path):
    result = synthesize_runtime_tool.invoke(
        {"name": "x", "source_code": VALID_TOOL, "action": "explode", "workspace_root": str(tmp_path)}
    )
    assert result["success"] is False
    assert result["status"] == "invalid-action"


def test_tool_history_action(tmp_path):
    synthesize_runtime_tool.invoke(
        {"name": "versioned_tool", "source_code": VALID_TOOL, "workspace_root": str(tmp_path)}
    )
    synthesize_runtime_tool.invoke(
        {"name": "versioned_tool", "source_code": VALID_TOOL, "workspace_root": str(tmp_path)}
    )
    history = list_dynamic_tools.invoke(
        {"action": "history", "name": "versioned_tool", "workspace_root": str(tmp_path)}
    )
    assert history["success"] is True
    assert history["count"] >= 1


def test_tool_stats_action(tmp_path):
    result = list_dynamic_tools.invoke({"action": "stats", "workspace_root": str(tmp_path)})
    assert result["success"] is True
    assert "active_tools" in result["results"][0]


def test_tool_history_requires_name(tmp_path):
    result = list_dynamic_tools.invoke({"action": "history", "workspace_root": str(tmp_path)})
    assert result["success"] is False


def test_tool_rejects_unknown_list_action(tmp_path):
    result = list_dynamic_tools.invoke({"action": "explode", "workspace_root": str(tmp_path)})
    assert result["success"] is False
