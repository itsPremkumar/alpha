"""Tests for Skill-Embedded On-Demand MCP Lifecycle Manager."""

from agent_workspace.skills.mcp_lifecycle import SkillMcpLifecycleManager

SKILL_WITH_MCP = """---
name: playwright-browser
description: Web automation with Playwright
mcp-servers:
  playwright:
    command: npx
    args:
      - "@modelcontextprotocol/server-playwright"
  context7:
    command: npx
    args:
      - "@context7/server"
---

# Playwright Browser Automation
Use the embedded MCP tools to navigate and inspect web applications.
"""


def test_parse_frontmatter_mcp_specs():
    manager = SkillMcpLifecycleManager()
    specs = manager.parse_skill_mcp_specs(SKILL_WITH_MCP)
    assert len(specs) == 2
    names = [s.server_name for s in specs]
    assert "playwright" in names
    assert "context7" in names


def test_acquire_and_release_lifecycle():
    manager = SkillMcpLifecycleManager()
    assert len(manager.get_active_servers()) == 0

    # Acquire servers for the active skill
    acquired = manager.acquire_for_skill("playwright-browser", SKILL_WITH_MCP)
    assert "playwright" in acquired
    assert "context7" in acquired
    assert len(manager.get_active_servers()) == 2

    # Release servers when skill finishes
    released = manager.release_for_skill("playwright-browser")
    assert "playwright" in released
    assert "context7" in released
    assert len(manager.get_active_servers()) == 0
