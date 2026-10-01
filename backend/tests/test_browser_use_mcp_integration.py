"""Browser Use MCP integration: configuration contract.

Browser Use is wired as a **stdio MCP server launched through `uvx`**, not as a
dependency of Alpha. That is not a packaging preference, it is a correctness
requirement, and this test exists so a future edit cannot quietly undo it.

Why `uvx` and not a pyproject extra
-----------------------------------
`browser-use` declares its runtime with **exact `==` pins** on 61 transitive
dependencies. Measured against Alpha's locked set, six conflict:

    pydantic    alpha 2.13.3  vs  browser-use 2.13.5
    openai      alpha 2.32.0  vs  browser-use 2.26.0   (downgrade)
    anthropic   alpha 0.97.0  vs  browser-use 0.76.0   (major downgrade)
    anyio       alpha 4.13.0  vs  browser-use 4.12.1
    click       alpha 8.5.0   vs  browser-use 8.3.3
    requests    alpha 2.33.1  vs  browser-use 2.33.0

Installing it into `backend/.venv` would downgrade Alpha's provider SDKs, which
the model layer depends on. `uvx --from 'browser-use[cli]'` resolves a separate
environment per invocation, so the pins can never touch Alpha's lockfile.

Why the block ships disabled
----------------------------
Enabling it starts a real Chromium and, on first run, resolves the whole uvx
environment. That is a runtime cost and a network fetch the operator should opt
into deliberately, so the shipped template is `enabled: false` like every other
optional server in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_CONFIG = _REPO_ROOT / "extensions_config.example.json"

# The documented upstream invocation. Kept literal: `uvx --from browser-use[cli]`
# is what installs the CLI extras the MCP server needs, and dropping either the
# `--from` or the `[cli]` extra produces a "CLI addon is not installed" failure
# at session-init time rather than at install time.
_EXPECTED_COMMAND = "uvx"
_EXPECTED_ARGS = ["--from", "browser-use[cli]", "browser-use", "--mcp"]

# browser-use's own agent is the last-resort autonomous path; the direct-control
# tools are the ones an operator reaches for first. Both arrive over the same
# MCP server, so the description has to say which is which -- otherwise a reader
# assumes the autonomous agent runs on every navigation.
_EXPECTED_AGENT_TOOL = "retry_with_browser_use_agent"

# capability -> accepted phrasings in the description prose. These are checked as
# concepts, not as verbatim tool names: a description that says "extract" rather
# than "extract_content" still tells an operator what the server does, and forcing
# exact identifiers would make the test a formatting lint rather than a contract.
_EXPECTED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "navigate": ("navigate",),
    "click": ("click",),
    "type": ("type",),
    "inspect page state": ("get_state", "page state", "inspect"),
    "extract content": ("extract",),
    "screenshot": ("screenshot",),
}


def _load_example_config() -> dict:
    return json.loads(_EXAMPLE_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def browser_use_block() -> dict:
    servers = _load_example_config().get("mcpServers", {})
    assert "browser-use" in servers, "extensions_config.example.json must ship a 'browser-use' MCP server entry; without it the shipped template cannot describe the Browser Use integration"
    return servers["browser-use"]


def test_uses_uvx_so_dependency_pins_cannot_reach_alpha(browser_use_block: dict) -> None:
    """The isolation guarantee, pinned.

    If someone 'simplifies' this to `python -m browser_use` or moves the package
    into a pyproject extra, browser-use's `==` pins would resolve into Alpha's
    venv and downgrade openai/anthropic/pydantic. This assertion is the tripwire.
    """
    assert browser_use_block["command"] == _EXPECTED_COMMAND
    assert browser_use_block["args"] == _EXPECTED_ARGS
    assert browser_use_block["type"] == "stdio"


def test_ships_disabled_by_default(browser_use_block: dict) -> None:
    """Enabling starts a real browser and resolves an environment on first use."""
    assert browser_use_block["enabled"] is False


def test_timeouts_accommodate_first_start_and_slow_browsing(browser_use_block: dict) -> None:
    """A browse is slow; the first uvx resolve is slower.

    Defaults of 60s would fail session init on a cold uvx cache and time out any
    genuinely multi-step navigation.
    """
    assert browser_use_block["session_init_timeout"] >= 300
    assert browser_use_block["tool_call_timeout"] >= 600


def test_prefixes_tool_names_to_avoid_collision_with_native_browser_tools(
    browser_use_block: dict,
) -> None:
    """Alpha already ships `browser_navigate_and_inspect`.

    browser-use exposes `browser_navigate`, `browser_click`, and friends as MCP
    tools. Without a prefix those sit beside a native tool with a near-identical
    name and the model cannot tell the two reasoning layers apart.
    """
    assert browser_use_block["tool_name_prefix"] is True


def test_env_references_are_variables_not_literal_secrets(browser_use_block: dict) -> None:
    """Model credentials come from the environment, never from committed JSON."""
    env = browser_use_block["env"]
    assert env, "browser-use needs a model endpoint configured"

    for key, value in env.items():
        assert isinstance(value, str), f"{key} must be a string"
        if key.endswith("API_KEY"):
            assert value.startswith("$"), f"{key} must be an environment reference like $OPENROUTER_API_KEY, not a literal credential"

    # Reuses a key Alpha already configures, so enabling this needs no new
    # provider account. browser-use speaks the OpenAI-compatible protocol.
    assert env["OPENAI_API_KEY"].startswith("$")
    assert env["OPENAI_BASE_URL"].startswith("https://")


def test_routing_is_keyword_gated_so_tools_stay_out_of_unrelated_turns(
    browser_use_block: dict,
) -> None:
    """A 17-tool browser group must not appear on every prompt.

    Routing keeps the heavy browsing surface attached to browsing-intent turns.
    """
    routing = browser_use_block["routing"]
    assert routing["mode"] == "prefer"

    keywords = {k.lower() for k in routing["keywords"]}
    assert {"browser", "navigate"} <= keywords, "routing keywords must cover the core browsing intents so the server is selected for browse/navigate requests"


def test_description_distinguishes_autonomous_agent_from_direct_control(
    browser_use_block: dict,
) -> None:
    """The autonomous agent is a fallback, not the default path.

    An agent that plans and clicks autonomously against a page it fetched is the
    risky path (indirect prompt injection); the direct tools are not. The operator
    reading this config has to be able to tell them apart without opening docs.
    """
    description = browser_use_block["description"].lower()

    assert _EXPECTED_AGENT_TOOL.lower() in description, "description must name the autonomous agent tool so its blast radius is visible"
    assert "last-resort" in description or "last resort" in description, "description must mark the autonomous agent as a fallback"

    for capability, aliases in _EXPECTED_CAPABILITIES.items():
        assert any(alias in description for alias in aliases), f"description should mention the direct-control capability '{capability}' so operators know browsing does not require the autonomous agent"


def test_json_uses_expandable_env_reference_syntax(browser_use_block: dict) -> None:
    """`$VAR` is Alpha's expansion syntax for MCP server env blocks.

    A literal here would silently ship a credential; a different placeholder
    (`${VAR}`, `%VAR%`) would not be expanded by the loader at all.
    """
    for key, value in browser_use_block["env"].items():
        if key.endswith("API_KEY"):
            assert not value.startswith("${"), "Alpha MCP env uses $VAR, not ${VAR}"
            assert not value.startswith("%"), "Alpha MCP env uses $VAR, not Windows %VAR%"
