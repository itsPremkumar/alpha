"""Cua Driver MCP integration: configuration contract.

Cua Driver (https://github.com/trycua/cua, MIT for the driver) is wired as an
**operator-installed binary launched over stdio MCP**, not as an Alpha dependency.
This test pins the shipped template block so a future edit cannot quietly undo the
five decisions behind it, and so the description cannot drift into claiming a safety
net that does not exist.

Why a binary and not `uvx`/`npx`
--------------------------------
The driver is a standalone cross-platform binary with its own runtime, daemon and
OS permission grants (Accessibility/Screen Recording on macOS). Alpha must not manage
its install: the operator installs it, runs `cua-driver doctor`, and grants
permissions interactively. That is the opposite of `browser-use`, where exact `==`
dependency pins forced an isolated `uvx` environment (see
``test_browser_use_mcp_integration.py``) - no Python environment is involved here at
all.

A consequence worth stating: ``_DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST`` in
``app/gateway/routers/mcp.py`` is ``{npx, uvx}``. A **config file** may express any
command, so the shipped template works; registering or enabling this server through
the **Gateway API** requires ``ALPHA_MCP_STDIO_COMMAND_ALLOWLIST=cua-driver``. The
description says so, and this test keeps it saying so.

Why the block ships disabled
----------------------------
Enabling it gives a process the ability to send input to every application in the OS
user session. That is an operator decision taken on a machine where the binary exists
and permissions are granted - never a default-on capability.

Why the description must say what it says
-----------------------------------------
MCP tools cannot self-classify governance, so every ``cua-driver_*`` tool lands in the
elevated class (``WRITE`` / ``ASK`` / unknown reversibility) with
``provenance=elevated:untrusted_source``, and ``SentinelGuard`` - which wraps only
``alpha.computer_use.dispatcher`` - does not gate MCP-delivered input. A description
that omits either fact implies guards that are not there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_CONFIG = _REPO_ROOT / "extensions_config.example.json"

# The documented upstream invocation: one binary, one subcommand. Kept literal so a
# "modernisation" to `uvx cua-driver` or `npx cua-driver` fails here rather than at
# session-init time on a machine where neither exists.
_EXPECTED_COMMAND = "cua-driver"
_EXPECTED_ARGS = ["mcp"]

# Concepts the description must carry. Checked as accepted phrasings rather than
# verbatim identifiers: the point is that an operator reading the config learns what
# the server does and what it does not guard, not that the prose matches a lint.
_EXPECTED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "window inspection": ("snapshot", "accessibility tree"),
    "input": ("pointer", "keyboard"),
    "no model inside": ("never calls a model", "no provider key"),
    "separate install": ("operator-installed", "install it separately"),
}

# Governance and safety facts that must stay visible in the shipped template.
_EXPECTED_GOVERNANCE_CONCEPTS: dict[str, tuple[str, ...]] = {
    "elevated MCP governance default": ("elevated governance class", "ask-per-call"),
    "sentinel does not wrap MCP input": ("sentinelguard does not wrap",),
    "permission mode named": ("cua_driver_permission_mode",),
    "API allowlist named": ("alpha_mcp_stdio_command_allowlist",),
    "AGPL extension warning": ("agpl",),
}


def _load_example_config() -> dict:
    return json.loads(_EXAMPLE_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cua_driver_block() -> dict:
    servers = _load_example_config().get("mcpServers", {})
    assert "cua-driver" in servers, "extensions_config.example.json must ship a 'cua-driver' MCP server entry; without it the shipped template cannot describe the Cua Driver integration"
    return servers["cua-driver"]


def test_uses_the_installed_binary_over_stdio(cua_driver_block: dict) -> None:
    """One binary, one subcommand, stdio transport.

    If this ever becomes `uvx`/`npx`, the block is describing a package that does not
    exist in either registry under that name; the real integration is an
    operator-installed driver.
    """
    assert cua_driver_block["command"] == _EXPECTED_COMMAND
    assert cua_driver_block["args"] == _EXPECTED_ARGS
    assert cua_driver_block["type"] == "stdio"


def test_ships_disabled_by_default(cua_driver_block: dict) -> None:
    """Enabling grants input to every application in the OS user session."""
    assert cua_driver_block["enabled"] is False


def test_timeouts_bound_bring_up_and_slow_gestures(cua_driver_block: dict) -> None:
    """First start may spawn a runtime/daemon and wait on OS permissions.

    The 60s default is too tight for that; but unbounded is not an option either, or
    a hung driver blocks agent construction - so both bounds are explicit here.
    """
    first_start = cua_driver_block["session_init_timeout"]
    per_call = cua_driver_block["tool_call_timeout"]
    assert first_start is not None and per_call is not None, "both bounds must be explicit; None means unbounded, and a hung driver would then block agent construction"
    assert first_start >= 120
    assert per_call >= 120


def test_prefixes_tool_names_to_avoid_collision_with_native_desktop_tools(
    cua_driver_block: dict,
) -> None:
    """Alpha already ships five `desktop_*` tools.

    Without a prefix, `list_apps`/`click`/`get_window_state` sit beside
    `desktop_screenshot` and `desktop_mouse_action` with nothing telling the model
    which safety layer each one passes through - and two servers could publish the
    same bare tool name.
    """
    assert cua_driver_block["tool_name_prefix"] is True


def test_surfaces_structured_content_so_the_drivers_own_handles_are_reachable(cua_driver_block: dict) -> None:
    """The driver's preferred addressing lives only in `structuredContent`.

    `get_window_state` returns the tree as Markdown and keeps the `element_token`
    handles and the `capture_id` in `structuredContent`, which LangChain carries as a
    `ToolMessage` artifact **no model is shown**. Leaving this off would mean the model
    can only ever act by guessed pixels — the path the driver itself labels the weaker
    one. The flag is per-server and opt-in everywhere else, so turning it on here says
    nothing about other servers.
    """
    assert cua_driver_block["include_structured_content"] is True

    other_servers = {name: block for name, block in _load_example_config()["mcpServers"].items() if name != "cua-driver"}
    assert other_servers, "the example config should ship other MCP servers for this comparison to mean anything"
    for name, block in other_servers.items():
        assert block.get("include_structured_content") in (None, False), f"only the server that needs it opts in; '{name}' must not be opted in by this block's presence"


def test_env_declares_permission_mode_as_a_literal_not_a_secret(cua_driver_block: dict) -> None:
    """`CUA_DRIVER_PERMISSION_MODE` names a mode, it is not a credential.

    It must be a plain value (Alpha expands `$VAR` on load, so a literal starting
    with `$` would be treated as an environment reference and resolve to `""`),
    and it must stay visible rather than silently inheriting upstream's default.
    """
    env = cua_driver_block["env"]
    assert env["CUA_DRIVER_PERMISSION_MODE"] == "standard"
    for key, value in env.items():
        assert isinstance(value, str), f"{key} must be a string"
        assert not key.endswith("API_KEY"), "Cua Driver never calls a model, so this block must not grow a provider key"
        assert not value.startswith("$"), f"{key} is a mode selector, not an environment reference"


def test_routing_is_keyword_gated_and_does_not_snipe_browser_intents(
    cua_driver_block: dict,
) -> None:
    """A ~50-tool desktop group must not appear on every prompt.

    The keywords are deliberately desktop-shaped. `browser-use` already claims
    `screenshot`/`click`/`extract` for page work, so both servers matching the same
    turn would leave the model choosing between two observation models with no hint
    which is meant.
    """
    routing = cua_driver_block["routing"]
    assert routing["mode"] == "prefer"

    keywords = {k.lower() for k in routing["keywords"]}
    assert {"desktop", "window", "mouse", "keyboard"} <= keywords, "routing keywords must cover the core desktop intents so the server is selected for OS-level requests"

    browser_use = _load_example_config()["mcpServers"]["browser-use"]
    browser_keywords = {k.lower() for k in browser_use["routing"]["keywords"]}
    overlap = keywords & browser_keywords
    assert not overlap, f"desktop routing must not claim the same keywords as browser-use (overlap: {sorted(overlap)}); prefer-mode hints for two different observation models on one turn are ambiguous"


def test_description_states_capabilities_and_install_shape(cua_driver_block: dict) -> None:
    """An operator reading only this block must learn what it controls and how it arrives."""
    description = cua_driver_block["description"].lower()

    for capability, aliases in _EXPECTED_CAPABILITIES.items():
        assert any(alias in description for alias in aliases), f"description should mention '{capability}' so operators know what the server does"


def test_description_keeps_governance_and_safety_boundaries_visible(
    cua_driver_block: dict,
) -> None:
    """The two facts that are easy to lose in a rewrite: what is *not* guarded.

    MCP tools default to the elevated governance class, and SentinelGuard wraps only
    `alpha.computer_use.dispatcher` - it never sees an MCP-delivered click. Dropping
    either sentence turns the description into an implied safety guarantee.
    """
    description = cua_driver_block["description"].lower()

    for fact, aliases in _EXPECTED_GOVERNANCE_CONCEPTS.items():
        assert any(alias in description for alias in aliases), f"description must keep the '{fact}' boundary visible so enabling this server cannot read as guarded-by-sentinel or auto-approved"

    # Honesty about the licensing surface: the driver is MIT, the optional perception
    # extensions are not, and only the MIT surface may be installed here.
    assert "agpl" in description
    assert "docs/research_computer_use_cua.md" in description, "description must point at the research document that carries the licensing table and the runbook"


def test_json_uses_expandable_env_reference_syntax(cua_driver_block: dict) -> None:
    """`$VAR` is Alpha's expansion syntax for MCP server env blocks.

    A different placeholder (`${VAR}`, `%VAR%`) would not be expanded by the loader
    at all, and a bare secret here would ship in git.
    """
    for key, value in cua_driver_block["env"].items():
        if key.endswith("API_KEY"):
            assert not value.startswith("${"), "Alpha MCP env uses $VAR, not ${VAR}"
            assert not value.startswith("%"), "Alpha MCP env uses $VAR, not Windows %VAR%"
